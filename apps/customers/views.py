from django.shortcuts import render, redirect, get_object_or_404
from django.views.generic import ListView, DetailView, CreateView, UpdateView, View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.db.models import Sum, Q
from django.urls import reverse_lazy
from django.db import transaction

from .models import Customer, CustomerTransaction
from .forms import CustomerForm, CustomerPaymentForm
from apps.core.mixins import PaginationMixin

class CustomerListView(LoginRequiredMixin, PaginationMixin, ListView):
    model = Customer
    template_name = 'customers/customer_list.html'
    context_object_name = 'customers'

    def get_queryset(self):
        user = self.request.user
        queryset = Customer.objects.filter(tenant=user.tenant).select_related('shop')
        
        # Role config
        role_name = user.role.name if user.role else 'ATTENDANT'
        if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER']:
             # Shop managers and cashiers only see their shop's customers
             queryset = queryset.filter(shop=user.location)
        
        search_query = self.request.GET.get('search')
        if search_query:
            queryset = queryset.filter(
                Q(name__icontains=search_query) | 
                Q(phone__icontains=search_query) |
                Q(email__icontains=search_query)
            )
        return queryset

class CustomerCreateView(LoginRequiredMixin, CreateView):
    model = Customer
    form_class = CustomerForm
    template_name = 'customers/customer_form.html'
    success_url = reverse_lazy('customers:customer_list')

    def dispatch(self, request, *args, **kwargs):
        # Only Shop Managers, Cashiers and Admins can create customers
        role = request.user.role.name if request.user.role else None
        if role not in ['SHOP_MANAGER', 'ADMIN', 'SHOP_CASHIER']:
            messages.error(request, "Only shop managers can add customers.")
            return redirect('customers:customer_list')
        return super().dispatch(request, *args, **kwargs)
    
    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        user = self.request.user
        role_name = user.role.name if user.role else 'ATTENDANT'
        
        # If not admin, hide shop field
        if role_name != 'ADMIN':
             if 'shop' in form.fields:
                 # Either remove it or make it hidden. 
                 # Better to remove and set in form_valid
                 del form.fields['shop']
        else:
             # Admin can see shops
             from apps.core.models import Location
             form.fields['shop'].queryset = Location.objects.filter(
                 tenant=user.tenant, 
                 location_type='SHOP'
             )
        return form

    def form_valid(self, form):
        form.instance.tenant = self.request.user.tenant
        
        # Auto-assign shop for non-admins
        user = self.request.user
        role_name = user.role.name if user.role else 'ATTENDANT'
        if role_name != 'ADMIN':
            form.instance.shop = user.location
            
        messages.success(self.request, "Customer created successfully.")
        return super().form_valid(form)

class CustomerUpdateView(LoginRequiredMixin, UpdateView):
    model = Customer
    form_class = CustomerForm
    template_name = 'customers/customer_form.html'
    success_url = reverse_lazy('customers:customer_list')

    def dispatch(self, request, *args, **kwargs):
        # Only Shop Managers, Cashiers and Admins can edit customers
        role = request.user.role.name if request.user.role else None
        if role not in ['SHOP_MANAGER', 'ADMIN', 'SHOP_CASHIER']:
            messages.error(request, "Only shop managers can edit customers.")
            return redirect('customers:customer_list')
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        qs = Customer.objects.filter(tenant=self.request.user.tenant)
        user = self.request.user
        role_name = user.role.name if user.role else 'ATTENDANT'
        
        if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER']:
            qs = qs.filter(shop=user.location)
            
        return qs
        
    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        user = self.request.user
        role_name = user.role.name if user.role else 'ATTENDANT'
        
        if role_name != 'ADMIN':
             if 'shop' in form.fields:
                 del form.fields['shop']
        else:
             from apps.core.models import Location
             form.fields['shop'].queryset = Location.objects.filter(
                 tenant=user.tenant, 
                 location_type='SHOP'
             )
        return form

    def form_valid(self, form):
        messages.success(self.request, "Customer updated successfully.")
        return super().form_valid(form)

class CustomerDetailView(LoginRequiredMixin, DetailView):
    model = Customer
    template_name = 'customers/customer_detail.html'
    context_object_name = 'customer'

    def get_queryset(self):
        qs = Customer.objects.filter(tenant=self.request.user.tenant)
        user = self.request.user
        role_name = user.role.name if user.role else 'ATTENDANT'
        if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER']:
            qs = qs.filter(shop=user.location)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['transactions'] = self.object.transactions.select_related('performed_by').order_by('-created_at')[:50]
        context['payment_form'] = CustomerPaymentForm(
            tenant=self.request.user.tenant,
            initial={'customer_phone': self.object.phone}
        )
        
        # Pass payment receipt to context and then clear from session
        context['open_payment_receipt'] = self.request.session.pop('open_payment_receipt', None)
        
        # Build ct_provider_map for E-Cash customer transactions
        transactions = context['transactions']
        if transactions:
            from apps.payments.models import ECashLedger
            ct_ids = [t.id for t in transactions if 'ECASH' in (t.description or '').upper()]
            if ct_ids:
                ledger_entries = ECashLedger.objects.filter(
                    tenant=self.request.user.tenant,
                    reference_type='CustomerTransaction',
                    reference_id__in=ct_ids
                ).select_related('provider_config')
                
                context['ct_provider_map'] = {
                    entry.reference_id: entry.provider_config.nickname if entry.provider_config else 'E-Cash'
                    for entry in ledger_entries
                }
            else:
                context['ct_provider_map'] = {}

        # Check if any E-Cash provider is enabled for this tenant
        from apps.payments.models import PaymentProviderConfig
        active_provider = PaymentProviderConfig.objects.filter(
            tenant=self.request.user.tenant,
            is_active=True
        ).exists()
        context['paystack_enabled'] = active_provider
        
        return context

class CustomerPaymentView(LoginRequiredMixin, View):
    def post(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk, tenant=request.user.tenant)
        form = CustomerPaymentForm(request.POST, tenant=request.user.tenant)
        
        if form.is_valid():
            amount = form.cleaned_data['amount']
            description = form.cleaned_data['description'] or "Debt Repayment"
            method = form.cleaned_data['payment_method']
            provider_config_id = form.cleaned_data.get('provider_config')
            
            # Get shift for cash tracking
            from apps.sales.models import Shift
            shift = None
            if method == 'CASH':
                # Try user's location first, then customer's shop
                shop = request.user.location or customer.shop
                if shop:
                    shift = Shift.objects.filter(
                        tenant=request.user.tenant,
                        shop=shop,
                        attendant=request.user,
                        status='OPEN'
                    ).first()
            
            with transaction.atomic():
                # Update customer balance (Credit reduces debt/balance)
                # Debt is positive balance. Payment reduces it.
                balance_before = customer.current_balance
                customer.current_balance -= amount
                customer.save()
                
                # Create transaction record
                txn = CustomerTransaction.objects.create(
                    tenant=request.user.tenant,
                    customer=customer,
                    transaction_type='CREDIT', # Credit to account = Payment
                    amount=amount,
                    description=f"{description} ({method})",
                    reference_id=f"PMT-{customer.pk}-{customer.transactions.count() + 1}",
                    balance_before=balance_before,
                    balance_after=customer.current_balance,
                    performed_by=request.user
                )
                
                # Record E-Cash payments in the E-Cash Ledger
                # This ensures they go to ecash_balance, not cash_on_hand
                if method == 'ECASH':
                    from apps.payments.models import ECashLedger, PaymentProviderConfig
                    
                    provider_config = None
                    if provider_config_id:
                        provider_config = PaymentProviderConfig.objects.filter(
                            tenant=request.user.tenant, 
                            id=provider_config_id
                        ).first()
                        
                    ECashLedger.record_payment(
                        tenant=request.user.tenant,
                        amount=amount,
                        sale=None,  # No sale, this is a payment on account
                        paystack_ref='',
                        user=request.user,
                        notes=f"E-Cash payment from customer: {customer.name}",
                        shop=request.user.location or customer.shop,
                        provider_config=provider_config
                    )
            
            # Success message with receipt link
            messages.success(
                request, 
                f"Payment of {amount} recorded successfully. Receipt is opening in a new tab."
            )
            # Store transaction ID in session for JavaScript to open receipt
            request.session['open_payment_receipt'] = txn.pk
            return redirect('customers:customer_detail', pk=pk)
        else:
            messages.error(request, "Invalid payment details.")
            
        return redirect('customers:customer_detail', pk=pk)


class PaymentReceiptView(LoginRequiredMixin, DetailView):
    """Display payment receipt for a customer transaction."""
    model = CustomerTransaction
    template_name = 'customers/payment_receipt.html'
    context_object_name = 'transaction'
    
    def get_queryset(self):
        return CustomerTransaction.objects.filter(
            tenant=self.request.user.tenant,
            transaction_type='CREDIT'  # Only payments
        )
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['currency_symbol'] = self.request.user.tenant.currency_symbol
        context['tenant'] = self.request.user.tenant
        context['shop'] = self.request.user.location
        return context


class CustomerListExportView(LoginRequiredMixin, View):
    """Export customer list to Excel."""

    def get(self, request):
        from apps.core.excel_utils import create_export_workbook, build_excel_response

        user = request.user
        role_name = user.role.name if user.role else None

        # Only shop managers, cashiers, accountants, auditors, and admins can export
        if role_name not in ['SHOP_MANAGER', 'ACCOUNTANT', 'AUDITOR', 'ADMIN', 'SHOP_CASHIER']:
            messages.error(request, 'You do not have permission to export customers.')
            return redirect('customers:customer_list')

        queryset = Customer.objects.filter(tenant=user.tenant).select_related('shop')

        # Shop managers and cashiers only see their shop's customers
        if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER']:
            queryset = queryset.filter(shop=user.location)

        # Search filter
        search_query = request.GET.get('search')
        if search_query:
            queryset = queryset.filter(
                Q(name__icontains=search_query) |
                Q(phone__icontains=search_query) |
                Q(email__icontains=search_query)
            )

        headers = ['Name', 'Phone', 'Email', 'Shop', 'Credit Limit', 'Current Balance', 'Active']
        rows = []
        for c in queryset:
            rows.append([
                c.name,
                c.phone or '',
                c.email or '',
                c.shop.name if c.shop else '',
                float(c.credit_limit) if c.credit_limit else 'Unlimited',
                float(c.current_balance) if c.current_balance else 0,
                'Yes' if c.is_active else 'No',
            ])

        export_format = request.GET.get('format', 'excel')
        if export_format == 'pdf':
            from apps.core.pdf_utils import export_to_pdf
            shop_name = "All Shops"
            if role_name == 'SHOP_MANAGER':
                shop_name = user.location.name
                
            metadata = {
                'generator_name': user.get_full_name() or user.email,
                'shop_name': shop_name,
                'date_range': "All Time"
            }
            return export_to_pdf('customers_export.pdf', 'Customers List', headers, rows, metadata=metadata)
        else:
            wb = create_export_workbook('Customers', headers, rows)
            return build_excel_response(wb, 'customers_export.xlsx')

class CustomerCreditLedgerView(LoginRequiredMixin, PaginationMixin, ListView):
    """View customer credit ledger."""
    model = CustomerTransaction
    template_name = 'customers/credit_ledger.html'
    context_object_name = 'transactions'

    def dispatch(self, request, *args, **kwargs):
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['ACCOUNTANT', 'AUDITOR', 'ADMIN', 'SHOP_MANAGER', 'SHOP_CASHIER']:
            messages.error(request, 'You do not have permission to view the credit ledger.')
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user
        queryset = CustomerTransaction.objects.filter(
            tenant=user.tenant,
        ).select_related('customer', 'performed_by').order_by('-created_at')

        role_name = user.role.name if user.role else None
        if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER']:
            queryset = queryset.filter(performed_by__location=user.location)

        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        customer_id = self.request.GET.get('customer')

        if date_from:
            try:
                from datetime import datetime
                date_from_parsed = datetime.strptime(date_from, '%Y-%m-%d').date()
                queryset = queryset.filter(created_at__date__gte=date_from_parsed)
            except ValueError:
                pass

        if date_to:
            try:
                from datetime import datetime
                date_to_parsed = datetime.strptime(date_to, '%Y-%m-%d').date()
                queryset = queryset.filter(created_at__date__lte=date_to_parsed)
            except ValueError:
                pass

        if customer_id:
            queryset = queryset.filter(customer_id=customer_id)
            
        shop_id = self.request.GET.get('shop')
        if shop_id and role_name in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            queryset = queryset.filter(customer__shop_id=shop_id)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        # Customers dropdown for filtering
        customers = Customer.objects.filter(tenant=user.tenant)
        role_name = user.role.name if user.role else None
        if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER']:
            customers = customers.filter(shop=user.location)
        context['customers'] = customers.order_by('name')

        context['date_from'] = self.request.GET.get('date_from', '')
        context['date_to'] = self.request.GET.get('date_to', '')
        context['selected_customer'] = self.request.GET.get('customer', '')
        context['selected_shop'] = self.request.GET.get('shop', '')
        
        if role_name in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            from apps.core.models import Location
            context['shops'] = Location.objects.filter(tenant=user.tenant, location_type='SHOP', is_active=True)
            context['is_full_view'] = True
            
        # Compute net total from the filtered queryset
        from django.db.models import Sum, Q
        qs = self.get_queryset()
        
        # In CustomerTransaction, 'amount' is positive for both DEBT and PAYMENT.
        # Net Credit = Total DEBT - Total PAYMENT
        totals = qs.aggregate(
            total_debt=Sum('amount', filter=Q(transaction_type='DEBT')),
            total_payment=Sum('amount', filter=Q(transaction_type='PAYMENT'))
        )
        total_debt = totals['total_debt'] or 0
        total_payment = totals['total_payment'] or 0
        context['net_total'] = total_debt - total_payment
        
        return context

class CustomerCreditLedgerExportView(LoginRequiredMixin, View):
    """Export customer credit ledger."""

    def dispatch(self, request, *args, **kwargs):
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['ACCOUNTANT', 'AUDITOR', 'ADMIN', 'SHOP_MANAGER', 'SHOP_CASHIER']:
            messages.error(request, 'You do not have permission to export the credit ledger.')
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        from apps.core.excel_utils import create_export_workbook, build_excel_response
        user = request.user
        
        queryset = CustomerTransaction.objects.filter(
            tenant=user.tenant,
        ).select_related('customer', 'performed_by').order_by('-created_at')

        role_name = user.role.name if user.role else None
        if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER']:
            queryset = queryset.filter(performed_by__location=user.location)

        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        customer_id = self.request.GET.get('customer')

        if date_from:
            try:
                from datetime import datetime
                date_from_parsed = datetime.strptime(date_from, '%Y-%m-%d').date()
                queryset = queryset.filter(created_at__date__gte=date_from_parsed)
            except ValueError:
                pass

        if date_to:
            try:
                from datetime import datetime
                date_to_parsed = datetime.strptime(date_to, '%Y-%m-%d').date()
                queryset = queryset.filter(created_at__date__lte=date_to_parsed)
            except ValueError:
                pass

        if customer_id:
            queryset = queryset.filter(customer_id=customer_id)
            
        shop_id = self.request.GET.get('shop')
        if shop_id and role_name in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            queryset = queryset.filter(customer__shop_id=shop_id)

        headers = ['Date', 'Customer', 'Type', 'Amount', 'Description', 'Balance Before', 'Balance After', 'Performed By']
        rows = []
        for t in queryset:
            t_type = "Given (Debt Increased)" if t.transaction_type == 'DEBIT' else "Paid (Debt Decreased)"
            rows.append([
                t.created_at.strftime("%Y-%m-%d %H:%M"),
                t.customer.name,
                t_type,
                float(t.amount),
                t.description or '',
                float(t.balance_before),
                float(t.balance_after),
                t.performed_by.get_full_name() or t.performed_by.email
            ])

        export_format = request.GET.get('format', 'excel')
        if export_format == 'pdf':
            from apps.core.pdf_utils import export_to_pdf
            shop_name = "All Shops"
            if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER']:
                shop_name = user.location.name if user.location else ''
                
            metadata = {
                'generator_name': user.get_full_name() or user.email,
                'shop_name': shop_name,
                'date_range': f"{date_from or 'All Time'} to {date_to or 'All Time'}"
            }
            return export_to_pdf('credit_ledger_export.pdf', 'Customer Credit Ledger', headers, rows, metadata=metadata)
        else:
            wb = create_export_workbook('Credit Ledger', headers, rows)
            return build_excel_response(wb, 'credit_ledger_export.xlsx')
