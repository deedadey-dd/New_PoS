from apps.accounting.models import BankTransfer
from apps.core.excel_utils import create_export_workbook, build_excel_response
from apps.core.pdf_utils import export_to_pdf
from django.db.models import Sum
from datetime import datetime

class BankHistoryListView(LoginRequiredMixin, SortableMixin, ListView):
    """
    Bank history page.
    Shows bank transfers with date filtering, fund source filtering, and totals.
    """
    template_name = 'accounting/bank_history.html'
    context_object_name = 'bank_transfers'
    paginate_by = 30
    model = BankTransfer

    def get_queryset(self):
        user = self.request.user
        role_name = user.role.name if user.role else None
        
        qs = BankTransfer.objects.filter(tenant=user.tenant)
        if role_name not in ['SUPER_ADMIN', 'ADMIN', 'AUDITOR']:
            qs = qs.filter(accountant=user)
            
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        fund_source = self.request.GET.get('fund_source')
        
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        if fund_source:
            qs = qs.filter(fund_source=fund_source)
            
        return qs.order_by(*self.get_ordering())
        
    def get_ordering(self):
        sort_by = self.request.GET.get('sort', '-created_at')
        return [sort_by]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        
        context['date_from'] = self.request.GET.get('date_from', '')
        context['date_to'] = self.request.GET.get('date_to', '')
        context['selected_fund_source'] = self.request.GET.get('fund_source', '')
        
        context['total_deposited'] = qs.aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
        context['fund_choices'] = BankTransfer.FUND_CHOICES
        return context

class BankHistoryExportView(LoginRequiredMixin, View):
    """
    Export bank transfers history to Excel or PDF.
    """
    def get(self, request):
        user = request.user
        role_name = user.role.name if user.role else None
        
        qs = BankTransfer.objects.filter(tenant=user.tenant)
        if role_name not in ['SUPER_ADMIN', 'ADMIN', 'AUDITOR']:
            qs = qs.filter(accountant=user)
            
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')
        fund_source = request.GET.get('fund_source')
        
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        if fund_source:
            qs = qs.filter(fund_source=fund_source)
            
        sort_by = request.GET.get('sort', '-created_at')
        qs = qs.order_by(sort_by)
        
        export_format = request.GET.get('format', 'excel')
        headers = ['Date', 'Accountant', 'Fund Source', 'Platform', 'Teller Name', 'Notes', 'Amount']
        rows = []
        for bt in qs:
            provider = bt.provider_config.provider.name if bt.provider_config else ''
            rows.append([
                bt.created_at.strftime('%Y-%m-%d %H:%M'),
                bt.accountant.get_full_name() or bt.accountant.email,
                bt.get_fund_source_display(),
                provider,
                bt.teller_name,
                bt.notes,
                float(bt.amount)
            ])
            
        if export_format == 'pdf':
            metadata = {
                'generator_name': user.get_full_name() or user.email,
                'date_range': f"{date_from or 'All Time'} to {date_to or 'All Time'}"
            }
            return export_to_pdf('bank_history.pdf', 'Bank History', headers, rows, metadata=metadata)
        else:
            wb = create_export_workbook('Bank History', headers, rows)
            return build_excel_response(wb, 'bank_history.xlsx')
