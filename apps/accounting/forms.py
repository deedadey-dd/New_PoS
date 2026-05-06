"""
Forms for the accounting app.
"""
from decimal import Decimal
from django import forms
from django.db.models import Sum
from .models import CashTransfer
from apps.core.models import User


class CashTransferForm(forms.ModelForm):
    """Form for creating cash transfers."""
    
    class Meta:
        model = CashTransfer
        fields = ['amount', 'source_type', 'destination_type', 'to_user', 'notes']
        widgets = {
            'amount': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '0.00',
                'step': '0.01',
                'min': '0.01'
            }),
            'source_type': forms.Select(attrs={'class': 'form-select', 'id': 'id_source_type'}),
            'destination_type': forms.Select(attrs={'class': 'form-select', 'id': 'id_destination_type'}),
            'to_user': forms.Select(attrs={'class': 'form-select', 'id': 'id_to_user'}),
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
            
            # Setup default destination choices
            dest_choices = [('USER', 'User/Accountant'), ('BANK', 'Bank Account')]
            if 'destination_type' in self.fields:
                self.fields['destination_type'].choices = dest_choices
                
            self.fields['to_user'].required = False
            
            # Calculate cash on hand for validation
            if role_name not in ['ACCOUNTANT', 'ADMIN'] and 'source_type' in self.fields:
                self.fields.pop('source_type')

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
                # Add customer cash payments received (even outside shift)
                from apps.customers.models import CustomerTransaction
                customer_payments = CustomerTransaction.objects.filter(
                    tenant=user.tenant,
                    performed_by=user,
                    transaction_type='CREDIT',
                    description__icontains='(CASH)'
                ).exclude(
                    description__icontains='ECASH'
                ).exclude(
                    description__icontains='MOMO'
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                
                # Subtract expenses paid from shop's cash
                from apps.accounting.models import ExpenditureItem
                expenses = ExpenditureItem.objects.filter(
                    request__tenant=user.tenant,
                    request__requested_by=user,
                    status='APPROVED',
                    source_of_funds='SHOP_CASH'
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                
                # Subtract already transferred amounts
                transferred = CashTransfer.objects.filter(
                    tenant=user.tenant,
                    from_user=user,
                    status__in=['PENDING', 'CONFIRMED']
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                
                cash_on_hand += shiftless_cash + customer_payments
                self.cash_on_hand = max(Decimal('0'), cash_on_hand - transferred - expenses)
                
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
                    
                # Attendants cannot send to BANK
                self.fields['destination_type'].choices = [('USER', 'Shop Manager')]
                    
            elif role_name in ('SHOP_MANAGER', 'SHOP_CASHIER'):
                from apps.sales.models import Shift, Sale

                # Step 1: Cash received via CashTransfer (CONFIRMED).
                #   INCLUDES self-transfers (manager's own shift closes).
                #   When a manager closes a shift, a self-transfer is created
                #   for closing_cash. That becomes the canonical record for
                #   that shift's cash. We must NOT also count those same sales
                #   as own_sales — that would be double-counting.
                received = CashTransfer.objects.filter(
                    tenant=user.tenant,
                    to_user=user,
                    status='CONFIRMED'
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

                # Step 2: Cash sent upstream to accountant / bank.
                #   Excludes self-transfers (shift closes are not outgoing cash).
                sent_upstream = CashTransfer.objects.filter(
                    tenant=user.tenant,
                    from_user=user,
                    status__in=['PENDING', 'CONFIRMED']
                ).exclude(to_user=user).aggregate(total=Sum('amount'))['total'] or Decimal('0')

                # Step 3: Live cash from the CURRENT open shift.
                #   This shift is not yet closed so no self-transfer exists yet.
                #   Therefore it is NOT in 'received' — safe to add separately.
                open_shift = Shift.objects.filter(
                    tenant=user.tenant,
                    attendant=user,
                    status='OPEN'
                ).first()

                current_shift_cash = Decimal('0')
                if open_shift:
                    cs = open_shift.sales.filter(status='COMPLETED')
                    shift_cash = cs.filter(payment_method='CASH').aggregate(
                        total=Sum('total'))['total'] or Decimal('0')
                    shift_mixed = cs.filter(payment_method='MIXED').aggregate(
                        total=Sum('amount_paid'))['total'] or Decimal('0')
                    current_shift_cash = shift_cash + shift_mixed
                    if user.tenant.include_opening_cash_in_transfer:
                        current_shift_cash += open_shift.opening_cash

                # Step 4: Shiftless cash sales (shift__isnull=True).
                #   Sales with no shift never generate a self-transfer so they
                #   must be counted directly. Scoped to since last upstream
                #   transfer to prevent all-time bleed.
                last_transfer_dt = CashTransfer.objects.filter(
                    tenant=user.tenant,
                    from_user=user,
                    status__in=['PENDING', 'CONFIRMED']
                ).exclude(to_user=user).order_by('-created_at').values_list(
                    'created_at', flat=True
                ).first()

                shiftless_filter = dict(
                    tenant=user.tenant,
                    cashier=user,
                    status='COMPLETED',
                    shift__isnull=True
                )
                if last_transfer_dt:
                    shiftless_filter['created_at__gt'] = last_transfer_dt

                shiftless_cash = Sale.objects.filter(
                    **shiftless_filter, payment_method='CASH'
                ).aggregate(total=Sum('total'))['total'] or Decimal('0')
                shiftless_mixed = Sale.objects.filter(
                    **shiftless_filter, payment_method='MIXED'
                ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
                shiftless_sales = shiftless_cash + shiftless_mixed

                # Add customer cash payments received (payments on account)
                from apps.customers.models import CustomerTransaction
                customer_payments = CustomerTransaction.objects.filter(
                    tenant=user.tenant,
                    performed_by=user,
                    transaction_type='CREDIT',
                    description__icontains='(CASH)'
                ).exclude(
                    description__icontains='ECASH'
                ).exclude(
                    description__icontains='MOMO'
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

                # Subtract expenses paid from shop's cash
                from apps.accounting.models import ExpenditureItem
                expenses = ExpenditureItem.objects.filter(
                    request__tenant=user.tenant,
                    request__requested_by=user,
                    status='APPROVED',
                    source_of_funds='SHOP_CASH'
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

                self.cash_on_hand = max(
                    Decimal('0'),
                    received - sent_upstream
                    + current_shift_cash + shiftless_sales
                    + customer_payments - expenses
                )
                
                # Shop managers / cashiers can send to bank or accountants based on settings
                d_choices = []
                if user.tenant.allow_shop_to_accountant_transfers:
                    d_choices.append(('USER', 'Accountant'))
                if user.tenant.allow_shop_to_bank_transfers:
                    d_choices.append(('BANK', 'Bank Account'))
                
                if d_choices:
                    self.fields['destination_type'].choices = d_choices
                else:
                    self.fields['destination_type'].choices = [('', 'No destinations allowed (Contact Admin)')]
                
                self.fields['to_user'].queryset = User.objects.filter(
                    tenant=user.tenant,
                    is_active=True,
                    role__name='ACCOUNTANT'
                )
                self.fields['to_user'].label = "Send to Accountant"
                
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
                
            # Pre-populate amount with cash_on_hand and default to BANK for cashiers
            if not self.is_bound:
                if self.cash_on_hand and self.cash_on_hand > 0:
                    self.initial['amount'] = round(self.cash_on_hand, 2)
                if role_name == 'SHOP_CASHIER' and 'destination_type' in self.fields:
                    self.initial['destination_type'] = 'BANK'
        else:
            self.fields['to_user'].queryset = User.objects.none()
    
    def clean(self):
        cleaned_data = super().clean()
        dest_type = cleaned_data.get('destination_type')
        to_user = cleaned_data.get('to_user')
        amount = cleaned_data.get('amount')
        source_type = cleaned_data.get('source_type', 'CASH')
        
        if dest_type == 'USER' and not to_user:
            self.add_error('to_user', 'Please select a recipient.')
        elif dest_type == 'BANK':
            cleaned_data['to_user'] = None
            
        if amount:
            if source_type == 'CASH':
                if self.cash_on_hand is not None and amount > self.cash_on_hand:
                    self.add_error('amount', f"Insufficient funds. Your cash on hand is {self.cash_on_hand:.2f}. "
                                             f"You cannot transfer {amount:.2f}.")
            elif source_type in ['ECASH', 'MOMO']:
                if dest_type != 'BANK':
                    self.add_error('destination_type', "Digital wallets can only be transferred to a Bank Account.")
                from apps.payments.models import ECashLedger
                balance = ECashLedger.get_current_balance(self.user.tenant, wallet_type=source_type)
                if amount > balance:
                    self.add_error('amount', f"Insufficient {source_type} balance. Available is {balance:.2f}.")
            
        return cleaned_data

from .models import ExpenditureRequest, ExpenditureItem, ExpenditureCategory

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

