"""
Forms for the accounting app.
"""
from decimal import Decimal
from django import forms
from django.db.models import Sum
from .models import CashTransfer, ExpenditureCategory, ExpenditureRequest, ExpenditureItem
from apps.core.models import User


class CashTransferForm(forms.ModelForm):
    """Form for creating cash transfers."""
    
    class Meta:
        model = CashTransfer
        fields = ['amount', 'to_user', 'notes']
        widgets = {
            'amount': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '0.00',
                'step': '0.01',
                'min': '0.01'
            }),
            'to_user': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Reference or description (optional)',
                'rows': 2
            }),
        }
    
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.cash_on_hand = Decimal('0')
        
        if user and user.tenant:
            role_name = user.role.name if user.role else None
            
            # Calculate cash on hand for validation
            if role_name == 'SHOP_ATTENDANT':
                # Attendants can send to their shop manager
                from apps.sales.models import Shift, Sale
                
                cash_on_hand = Decimal('0')
                
                # 1. Cash from open shift
                open_shift = Shift.objects.filter(
                    tenant=user.tenant,
                    attendant=user,
                    status='OPEN'
                ).first()
                
                if open_shift:
                    # Cash from pure cash sales
                    cash_sales = Sale.objects.filter(
                        tenant=user.tenant,
                        shift=open_shift,
                        status='COMPLETED',
                        payment_method='CASH'
                    ).aggregate(total=Sum('total'))['total'] or Decimal('0')
                    # Cash portion from mixed payments (partial cash + credit)
                    mixed_cash = Sale.objects.filter(
                        tenant=user.tenant,
                        shift=open_shift,
                        status='COMPLETED',
                        payment_method='MIXED'
                    ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
                    shift_cash = cash_sales + mixed_cash
                    cash_on_hand += open_shift.opening_cash + shift_cash
                
                # 2. Cash from shiftless sales
                shiftless_cash_sales = Sale.objects.filter(
                    tenant=user.tenant,
                    attendant=user,
                    shift__isnull=True,
                    status='COMPLETED',
                    payment_method='CASH'
                ).aggregate(total=Sum('total'))['total'] or Decimal('0')
                shiftless_mixed = Sale.objects.filter(
                    tenant=user.tenant,
                    attendant=user,
                    shift__isnull=True,
                    status='COMPLETED',
                    payment_method='MIXED'
                ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
                shiftless_cash = shiftless_cash_sales + shiftless_mixed
                # 3. Customer cash payments received by this attendant
                from apps.customers.models import CustomerTransaction
                customer_payments = CustomerTransaction.objects.filter(
                    tenant=user.tenant,
                    performed_by=user,
                    transaction_type='CREDIT',
                    description__icontains='(CASH)'
                ).exclude(
                    description__icontains='ECASH'
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                cash_on_hand += shiftless_cash + customer_payments

                # Subtract ONLY manual transfers (not shift-close auto-transfers).
                # Shift-close transfers are tagged "Shift closing deposit - Shift #"
                # and are already excluded from the open-shift cash above, so
                # deducting them again would cause a false double-subtraction.
                transferred = CashTransfer.objects.filter(
                    tenant=user.tenant,
                    from_user=user,
                    status__in=['PENDING', 'CONFIRMED']
                ).exclude(
                    notes__startswith='Shift closing deposit'
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

                self.cash_on_hand = max(Decimal('0'), cash_on_hand - transferred)
                
                # Find their shop manager
                if user.location:
                    self.fields['to_user'].queryset = User.objects.filter(
                        tenant=user.tenant,
                        is_active=True,
                        location=user.location,
                        role__name='SHOP_MANAGER'
                    )
                    self.fields['to_user'].label = "Send to Shop Manager"
                else:
                    self.fields['to_user'].queryset = User.objects.none()
                    
            elif role_name in ['SHOP_MANAGER', 'SHOP_CASHIER']:
                from django.db.models import Q
                from apps.sales.models import Shift, Sale
                from apps.customers.models import CustomerTransaction
                
                received = CashTransfer.objects.filter(
                    tenant=user.tenant,
                    to_user=user,
                    status='CONFIRMED',
                    transfer_type='DEPOSIT'
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                
                sent = CashTransfer.objects.filter(
                    tenant=user.tenant,
                    from_user=user,
                    status__in=['CONFIRMED', 'PENDING'],
                    transfer_type__in=['DEPOSIT', 'EXPENDITURE'],
                ).exclude(
                    to_user=user
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                
                # Cash from current open shift
                open_shift_cash = Decimal('0')
                open_shift = Shift.objects.filter(
                    tenant=user.tenant,
                    attendant=user,
                    status='OPEN'
                ).first()
                
                if open_shift:
                    shift_cash_sales = Sale.objects.filter(
                        tenant=user.tenant,
                        shift=open_shift,
                        status='COMPLETED',
                        payment_method='CASH'
                    ).aggregate(total=Sum('total'))['total'] or Decimal('0')
                    shift_mixed = Sale.objects.filter(
                        tenant=user.tenant,
                        shift=open_shift,
                        status='COMPLETED',
                        payment_method='MIXED'
                    ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
                    open_shift_cash = open_shift.opening_cash + shift_cash_sales + shift_mixed
                
                # Own shiftless cash sales
                shiftless_cash_sales = Sale.objects.filter(
                    tenant=user.tenant,
                    attendant=user,
                    shift__isnull=True,
                    status='COMPLETED',
                    payment_method='CASH'
                ).aggregate(total=Sum('total'))['total'] or Decimal('0')
                shiftless_mixed = Sale.objects.filter(
                    tenant=user.tenant,
                    attendant=user,
                    shift__isnull=True,
                    status='COMPLETED',
                    payment_method='MIXED'
                ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
                own_sales = shiftless_cash_sales + shiftless_mixed
                
                # Customer payments on account
                customer_payments = CustomerTransaction.objects.filter(
                    tenant=user.tenant,
                    performed_by=user,
                    transaction_type='CREDIT',
                    description__icontains='(CASH)'
                ).exclude(
                    description__icontains='ECASH'
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                
                self.cash_on_hand = max(Decimal('0'), received - sent + open_shift_cash + own_sales + customer_payments)
                
                # Shop managers and cashiers can send to accountants
                accountants = User.objects.filter(
                    tenant=user.tenant,
                    is_active=True,
                    role__name='ACCOUNTANT'
                )
                if not accountants.exists():
                    accountants = User.objects.filter(
                        tenant=user.tenant,
                        is_active=True,
                        role__name='ADMIN'
                    )
                self.fields['to_user'].queryset = accountants
                self.fields['to_user'].label = "Select Accountant to Confirm"
                
            elif role_name == 'ACCOUNTANT':
                # Accountants can send to shop managers (if allowed)
                if user.tenant.allow_accountant_to_shop_transfers:
                    received = CashTransfer.objects.filter(
                        tenant=user.tenant,
                        to_user=user,
                        transfer_type='DEPOSIT',
                        status='CONFIRMED'
                    ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                    
                    self.cash_on_hand = received
                    
                    self.fields['to_user'].queryset = User.objects.filter(
                        tenant=user.tenant,
                        is_active=True,
                        role__name='SHOP_MANAGER'
                    )
                    self.fields['to_user'].label = "Send to Shop Manager"
                else:
                    self.fields['to_user'].queryset = User.objects.none()
            elif role_name == 'ADMIN':
                # Admins can send to anyone (no cash on hand limit)
                self.cash_on_hand = None  # No limit for admin
                self.fields['to_user'].queryset = User.objects.filter(
                    tenant=user.tenant,
                    is_active=True
                ).exclude(pk=user.pk)
            else:
                self.fields['to_user'].queryset = User.objects.none()
        else:
            self.fields['to_user'].queryset = User.objects.none()
    
    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        
        if amount and self.cash_on_hand is not None:
            if amount > self.cash_on_hand:
                raise forms.ValidationError(
                    f"Insufficient funds. Your cash on hand is {self.cash_on_hand:.2f}. "
                    f"You cannot transfer {amount:.2f}."
                )
        
        return amount

    def clean(self):
        cleaned_data = super().clean()
        if self.user and self.user.role:
            role_name = self.user.role.name
            # Managers, cashiers and attendants always send to accountant — never direct to bank
            if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER', 'SHOP_ATTENDANT']:
                cleaned_data['destination'] = 'ACCOUNTANT'
        return cleaned_data

class BankTransferForm(forms.ModelForm):
    class Meta:
        from .models import BankTransfer
        model = BankTransfer
        fields = ['amount', 'fund_source', 'teller_name', 'notes']
        widgets = {
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'fund_source': forms.Select(attrs={'class': 'form-select'}),
            'teller_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Name of bank teller'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Optional reference details'}),
        }
    
    provider_config = forms.ChoiceField(
        required=False,
        label="E-Cash Platform",
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_bank_provider_config'})
    )

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        # Populate provider choices
        choices = [('', '--- All E-Cash Platforms ---')]
        if self.user and self.user.tenant:
            role_name = self.user.role.name if self.user.role else None
            
            # Cashiers in strict workflow can only transfer physical cash to bank
            if role_name == 'SHOP_CASHIER':
                self.fields['fund_source'].choices = [('CASH', 'Cash')]
            
            from apps.payments.models import PaymentProviderConfig, ECashWithdrawal
            from django.db.models import Sum
            
            providers = PaymentProviderConfig.objects.filter(tenant=self.user.tenant, is_active=True)
            for p in providers:
                # Calculate available balance for this platform
                withdrawn = ECashWithdrawal.objects.filter(
                    tenant=self.user.tenant, 
                    provider_config=p, 
                    status='COMPLETED'
                ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
                
                # Subtract bank transfers for this platform
                banked = self.Meta.model.objects.filter(
                    tenant=self.user.tenant, 
                    fund_source='ECASH', 
                    provider_config=p
                ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
                
                available = max(Decimal('0'), withdrawn - banked)
                
                choices.append((p.id, f"{p.nickname} (Bal: {self.user.tenant.currency_symbol}{available:,.2f})"))
                
        self.fields['provider_config'].choices = choices
        
    def clean(self):
        cleaned_data = super().clean()
        amount = cleaned_data.get('amount')
        fund_source = cleaned_data.get('fund_source')
        
        if amount and fund_source and self.user:
            # Check balances
            from django.db.models import Sum, Q
            from .models import CashTransfer, BankTransfer, DigitalFundWithdrawal
            
            tenant = self.user.tenant
            role_name = self.user.role.name if self.user.role else None
            
            if fund_source == 'CASH':
                if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER']:
                    from apps.sales.models import Shift, Sale
                    from apps.customers.models import CustomerTransaction
                    
                    received = CashTransfer.objects.filter(
                        tenant=tenant, to_user=self.user, status='CONFIRMED', transfer_type='DEPOSIT'
                    ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                    
                    sent = CashTransfer.objects.filter(
                        tenant=tenant, from_user=self.user, status__in=['CONFIRMED', 'PENDING'], transfer_type__in=['DEPOSIT', 'EXPENDITURE']
                    ).exclude(to_user=self.user).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                    
                    open_shift_cash = Decimal('0')
                    open_shift = Shift.objects.filter(
                        tenant=tenant, attendant=self.user, status='OPEN'
                    ).first()
                    if open_shift:
                        shift_cash_sales = Sale.objects.filter(
                            tenant=tenant, shift=open_shift, status='COMPLETED', payment_method='CASH'
                        ).aggregate(total=Sum('total'))['total'] or Decimal('0')
                        shift_mixed = Sale.objects.filter(
                            tenant=tenant, shift=open_shift, status='COMPLETED', payment_method='MIXED'
                        ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
                        open_shift_cash = open_shift.opening_cash + shift_cash_sales + shift_mixed
                    
                    shiftless_cash_sales = Sale.objects.filter(
                        tenant=tenant, attendant=self.user, shift__isnull=True, status='COMPLETED', payment_method='CASH'
                    ).aggregate(total=Sum('total'))['total'] or Decimal('0')
                    shiftless_mixed = Sale.objects.filter(
                        tenant=tenant, attendant=self.user, shift__isnull=True, status='COMPLETED', payment_method='MIXED'
                    ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
                    own_sales = shiftless_cash_sales + shiftless_mixed
                    
                    customer_payments = CustomerTransaction.objects.filter(
                        tenant=tenant, performed_by=self.user, transaction_type='CREDIT', description__icontains='(CASH)'
                    ).exclude(description__icontains='ECASH').aggregate(total=Sum('amount'))['total'] or Decimal('0')
                    
                    banked = BankTransfer.objects.filter(tenant=tenant, accountant=self.user, fund_source='CASH').aggregate(total=Sum('amount'))['total'] or Decimal('0')
                    
                    available = received - sent + open_shift_cash + own_sales + customer_payments - banked
                else:
                    received = CashTransfer.objects.filter(tenant=tenant, to_user=self.user, status='CONFIRMED').aggregate(total=Sum('amount'))['total'] or Decimal('0')
                    sent = CashTransfer.objects.filter(tenant=tenant, from_user=self.user, status='CONFIRMED').aggregate(total=Sum('amount'))['total'] or Decimal('0')
                    banked = BankTransfer.objects.filter(tenant=tenant, accountant=self.user, fund_source='CASH').aggregate(total=Sum('amount'))['total'] or Decimal('0')
                    available = received - sent - banked
                
                if amount > available:
                    self.add_error('amount', f"Insufficient cash. You only have {available:.2f} available.")
                    
            elif fund_source == 'ECASH':
                provider_config_id = cleaned_data.get('provider_config')
                if provider_config_id:
                    from apps.payments.models import ECashLedger, PaymentProviderConfig
                    try:
                        provider = PaymentProviderConfig.objects.get(id=provider_config_id, tenant=tenant)
                        available = ECashLedger.get_provider_balance(tenant, provider)
                        
                        if amount > available:
                            self.add_error('amount', f"Insufficient E-Cash. {provider.nickname} only has {available:.2f} available.")
                    except PaymentProviderConfig.DoesNotExist:
                        pass
                else:
                    self.add_error('provider_config', "Please select an E-Cash platform to transfer from.")
                    
            elif fund_source == 'MOMO':
                withdrawn = DigitalFundWithdrawal.objects.filter(tenant=tenant, fund_source='MOMO').aggregate(total=Sum('amount'))['total'] or Decimal('0')
                banked = BankTransfer.objects.filter(tenant=tenant, fund_source='MOMO').aggregate(total=Sum('amount'))['total'] or Decimal('0')
                available = withdrawn - banked
                
                if amount > available:
                    self.add_error('amount', f"Insufficient Local Momo. You only have {available:.2f} available.")
                    
        return cleaned_data


# --- Expenditure Forms (ported from alpha) ---

class ExpenditureCategoryForm(forms.ModelForm):
    """Form for managing expenditure categories."""
    class Meta:
        model = ExpenditureCategory
        fields = ['name']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Category name'}),
        }


class ExpenditureRequestForm(forms.ModelForm):
    """Form for the parent expenditure voucher."""
    class Meta:
        model = ExpenditureRequest
        fields = ['notes']
        widgets = {
            'notes': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': 'Overall notes for this voucher (optional)...'
            }),
        }


class ExpenditureItemForm(forms.ModelForm):
    """Form for individual items in a voucher."""
    class Meta:
        model = ExpenditureItem
        fields = ['category', 'amount', 'description']
        widgets = {
            'category': forms.Select(attrs={'class': 'form-select'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control item-amount', 'step': '0.01', 'min': '0.01'}),
            'description': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'What was this for?'}),
        }

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant:
            self.fields['category'].queryset = ExpenditureCategory.objects.filter(
                tenant=tenant, is_active=True
            )
        self.fields['category'].empty_label = '— Category —'


# Inline formset for expenditure items
ExpenditureItemFormSet = forms.inlineformset_factory(
    ExpenditureRequest,
    ExpenditureItem,
    form=ExpenditureItemForm,
    extra=1,
    can_delete=True
)

