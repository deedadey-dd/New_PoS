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
                
                # Subtract already transferred amounts
                transferred = CashTransfer.objects.filter(
                    tenant=user.tenant,
                    from_user=user,
                    status__in=['PENDING', 'CONFIRMED']
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                
                cash_on_hand += shiftless_cash
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
                    
            elif role_name == 'SHOP_MANAGER':
                from apps.sales.models import Shift, Sale
                
                received = CashTransfer.objects.filter(
                    tenant=user.tenant,
                    to_user=user,
                    status='CONFIRMED'
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                
                sent = CashTransfer.objects.filter(
                    tenant=user.tenant,
                    from_user=user,
                    status='CONFIRMED'
                ).exclude(
                    to_user=user  # Exclude self-transfers (shift closings)
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
                
                self.cash_on_hand = received - sent + open_shift_cash + own_sales
                
                # Shop managers can only send to accountants
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
    
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        fund_source = self.cleaned_data.get('fund_source')
        
        if amount and fund_source and self.user:
            # Check balances
            from django.db.models import Sum, Q
            from apps.sales.models import Sale
            from apps.customers.models import CustomerTransaction
            from .models import CashTransfer, BankTransfer
            
            tenant = self.user.tenant
            
            if fund_source == 'CASH':
                received = CashTransfer.objects.filter(tenant=tenant, to_user=self.user, status='CONFIRMED').aggregate(total=Sum('amount'))['total'] or Decimal('0')
                sent = CashTransfer.objects.filter(tenant=tenant, from_user=self.user, status='CONFIRMED').aggregate(total=Sum('amount'))['total'] or Decimal('0')
                banked = BankTransfer.objects.filter(tenant=tenant, fund_source='CASH').aggregate(total=Sum('amount'))['total'] or Decimal('0')
                available = received - sent - banked
                
                if amount > available:
                    raise forms.ValidationError(f"Insufficient cash. You only have {available:.2f} available.")
                    
            elif fund_source == 'ECASH':
                acc_sales = Sale.objects.filter(tenant=tenant, status='COMPLETED', payment_method='ECASH', is_accountant_confirmed=True).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
                acc_ct = CustomerTransaction.objects.filter(tenant=tenant, transaction_type='CREDIT', description__icontains='ECASH', is_accountant_confirmed=True).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                banked = BankTransfer.objects.filter(tenant=tenant, fund_source='ECASH').aggregate(total=Sum('amount'))['total'] or Decimal('0')
                available = acc_sales + acc_ct - banked
                
                if amount > available:
                    raise forms.ValidationError(f"Insufficient E-Cash. You only have {available:.2f} available.")
                    
            elif fund_source == 'MOMO':
                acc_sales = Sale.objects.filter(tenant=tenant, status='COMPLETED', payment_method='MOMO', is_accountant_confirmed=True).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
                acc_ct = CustomerTransaction.objects.filter(tenant=tenant, transaction_type='CREDIT', description__icontains='MOMO', is_accountant_confirmed=True).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                banked = BankTransfer.objects.filter(tenant=tenant, fund_source='MOMO').aggregate(total=Sum('amount'))['total'] or Decimal('0')
                available = acc_sales + acc_ct - banked
                
                if amount > available:
                    raise forms.ValidationError(f"Insufficient Momo. You only have {available:.2f} available.")
                    
        return amount

