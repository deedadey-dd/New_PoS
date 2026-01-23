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
        if role_name == 'SHOP_MANAGER':
             # Shop managers only see their shop's customers
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
        # Only Shop Managers and Admins can create customers
        role = request.user.role.name if request.user.role else None
        if role not in ['SHOP_MANAGER', 'ADMIN']:
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
        # Only Shop Managers and Admins can edit customers
        role = request.user.role.name if request.user.role else None
        if role not in ['SHOP_MANAGER', 'ADMIN']:
            messages.error(request, "Only shop managers can edit customers.")
            return redirect('customers:customer_list')
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        qs = Customer.objects.filter(tenant=self.request.user.tenant)
        user = self.request.user
        role_name = user.role.name if user.role else 'ATTENDANT'
        
        if role_name == 'SHOP_MANAGER':
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
        return Customer.objects.filter(tenant=self.request.user.tenant)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['transactions'] = self.object.transactions.all().order_by('-created_at')[:50]
        context['payment_form'] = CustomerPaymentForm()
        
        # Pass payment receipt to context and then clear from session
        context['open_payment_receipt'] = self.request.session.pop('open_payment_receipt', None)
        
        # Check if Paystack E-Cash is enabled for this tenant
        from apps.payments.models import PaymentProviderSettings
        paystack_settings = PaymentProviderSettings.objects.filter(
            tenant=self.request.user.tenant,
            provider='PAYSTACK',
            is_active=True
        ).first()
        context['paystack_enabled'] = paystack_settings is not None
        
        return context

class CustomerPaymentView(LoginRequiredMixin, View):
    def post(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk, tenant=request.user.tenant)
        form = CustomerPaymentForm(request.POST)
        
        if form.is_valid():
            amount = form.cleaned_data['amount']
            description = form.cleaned_data['description'] or "Debt Repayment"
            method = form.cleaned_data['payment_method']
            
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
                    from apps.payments.models import ECashLedger
                    ECashLedger.record_payment(
                        tenant=request.user.tenant,
                        amount=amount,
                        sale=None,  # No sale, this is a payment on account
                        paystack_ref='',
                        user=request.user,
                        notes=f"E-Cash payment from customer: {customer.name}"
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
