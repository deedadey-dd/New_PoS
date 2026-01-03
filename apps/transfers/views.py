"""
Views for the transfers app.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.generic import ListView, DetailView, CreateView, View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy, reverse
from django.http import JsonResponse
from django.db import transaction

from .models import Transfer, TransferItem
from .forms import TransferForm, TransferItemFormSet, TransferItemForm, TransferReceiveForm, TransferDisputeForm, TransferCloseForm
from django.forms import inlineformset_factory
from apps.core.decorators import role_required
from apps.inventory.models import Batch, Product


class TransferListView(LoginRequiredMixin, ListView):
    """List all transfers for the tenant."""
    model = Transfer
    template_name = 'transfers/transfer_list.html'
    context_object_name = 'transfers'
    
    def get_queryset(self):
        queryset = Transfer.objects.filter(tenant=self.request.user.tenant)
        
        # Filter by status
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        
        # Filter by location (either source or destination)
        location = self.request.GET.get('location')
        if location:
            from django.db.models import Q
            queryset = queryset.filter(
                Q(source_location_id=location) | Q(destination_location_id=location)
            )
        
        return queryset.select_related(
            'source_location', 'destination_location', 'created_by'
        )
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.core.models import Location
        context['locations'] = Location.objects.filter(
            tenant=self.request.user.tenant, is_active=True
        )
        context['status_choices'] = Transfer.STATUS_CHOICES
        return context


class TransferCreateView(LoginRequiredMixin, View):
    """Create a new transfer."""
    template_name = 'transfers/transfer_form.html'
    
    def get(self, request):
        # Pass user to pre-populate source location
        form = TransferForm(tenant=request.user.tenant, user=request.user)
        
        # Create formset with 1 initial empty form
        TransferItemFormSetWithExtra = inlineformset_factory(
            Transfer,
            TransferItem,
            form=TransferItemForm,
            extra=1,  # 1 empty form
            can_delete=True,
            min_num=0,
            validate_min=False,
        )
        formset = TransferItemFormSetWithExtra(queryset=TransferItem.objects.none())
        
        # Filter products for each form in formset
        for item_form in formset:
            if hasattr(item_form.fields.get('product'), 'queryset'):
                item_form.fields['product'].queryset = Product.objects.filter(
                    tenant=request.user.tenant, is_active=True
                )
        
        return render(request, self.template_name, {
            'form': form,
            'formset': formset,
            'title': 'Create Transfer'
        })
    
    def post(self, request):
        form = TransferForm(request.POST, tenant=request.user.tenant)
        formset = TransferItemFormSet(request.POST)
        
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                transfer = form.save(commit=False)
                transfer.tenant = request.user.tenant
                transfer.created_by = request.user
                transfer.save()
                
                for item_form in formset:
                    if item_form.cleaned_data and not item_form.cleaned_data.get('DELETE', False):
                        item = item_form.save(commit=False)
                        item.tenant = request.user.tenant
                        item.transfer = transfer
                        item.save()
                
                messages.success(request, f"Transfer {transfer.transfer_number} created successfully!")
                return redirect('transfers:transfer_detail', pk=transfer.pk)
        
        return render(request, self.template_name, {
            'form': form,
            'formset': formset,
            'title': 'Create Transfer'
        })


class TransferDetailView(LoginRequiredMixin, DetailView):
    """View transfer details."""
    model = Transfer
    template_name = 'transfers/transfer_detail.html'
    context_object_name = 'transfer'
    
    def get_queryset(self):
        return Transfer.objects.filter(
            tenant=self.request.user.tenant
        ).select_related(
            'source_location', 'destination_location',
            'created_by', 'sent_by', 'received_by'
        ).prefetch_related('items__product', 'items__batch')


class TransferSendView(LoginRequiredMixin, View):
    """Send a draft transfer."""
    
    def post(self, request, pk):
        transfer = get_object_or_404(
            Transfer, pk=pk, tenant=request.user.tenant
        )
        
        try:
            transfer.send(request.user)
            messages.success(request, f"Transfer {transfer.transfer_number} sent successfully!")
        except Exception as e:
            messages.error(request, str(e))
        
        return redirect('transfers:transfer_detail', pk=pk)


class TransferReceiveView(LoginRequiredMixin, View):
    """Receive a sent transfer."""
    template_name = 'transfers/transfer_receive.html'
    
    def get(self, request, pk):
        transfer = get_object_or_404(
            Transfer, pk=pk, tenant=request.user.tenant
        )
        
        if not transfer.can_receive:
            messages.error(request, f"Cannot receive transfer in {transfer.status} status.")
            return redirect('transfers:transfer_detail', pk=pk)
        
        form = TransferReceiveForm(transfer=transfer)
        return render(request, self.template_name, {
            'transfer': transfer,
            'form': form
        })
    
    def post(self, request, pk):
        transfer = get_object_or_404(
            Transfer, pk=pk, tenant=request.user.tenant
        )
        
        if not transfer.can_receive:
            messages.error(request, f"Cannot receive transfer in {transfer.status} status.")
            return redirect('transfers:transfer_detail', pk=pk)
        
        form = TransferReceiveForm(request.POST, transfer=transfer)
        
        if form.is_valid():
            # Build items_received dict
            items_received = {}
            for item in transfer.items.all():
                items_received[str(item.pk)] = form.cleaned_data.get(f'received_{item.pk}', 0)
            
            try:
                with transaction.atomic():
                    transfer.receive(request.user, items_received)
                messages.success(request, f"Transfer {transfer.transfer_number} received!")
            except Exception as e:
                messages.error(request, str(e))
        
        return redirect('transfers:transfer_detail', pk=pk)


class TransferDisputeView(LoginRequiredMixin, View):
    """Dispute a transfer."""
    template_name = 'transfers/transfer_dispute.html'
    
    def get(self, request, pk):
        transfer = get_object_or_404(
            Transfer, pk=pk, tenant=request.user.tenant
        )
        
        if not transfer.can_dispute:
            messages.error(request, f"Cannot dispute transfer in {transfer.status} status.")
            return redirect('transfers:transfer_detail', pk=pk)
        
        form = TransferDisputeForm()
        return render(request, self.template_name, {
            'transfer': transfer,
            'form': form
        })
    
    def post(self, request, pk):
        transfer = get_object_or_404(
            Transfer, pk=pk, tenant=request.user.tenant
        )
        
        form = TransferDisputeForm(request.POST)
        
        if form.is_valid():
            try:
                transfer.dispute(request.user, form.cleaned_data['dispute_reason'])
                messages.warning(request, f"Transfer {transfer.transfer_number} marked as disputed.")
            except Exception as e:
                messages.error(request, str(e))
        
        return redirect('transfers:transfer_detail', pk=pk)


class TransferCloseView(LoginRequiredMixin, View):
    """Close a transfer."""
    
    def post(self, request, pk):
        transfer = get_object_or_404(
            Transfer, pk=pk, tenant=request.user.tenant
        )
        
        resolution_notes = request.POST.get('resolution_notes', '')
        
        try:
            transfer.close(request.user, resolution_notes)
            messages.success(request, f"Transfer {transfer.transfer_number} closed.")
        except Exception as e:
            messages.error(request, str(e))
        
        return redirect('transfers:transfer_detail', pk=pk)


class TransferCancelView(LoginRequiredMixin, View):
    """Cancel a draft transfer."""
    
    def post(self, request, pk):
        transfer = get_object_or_404(
            Transfer, pk=pk, tenant=request.user.tenant
        )
        
        try:
            transfer.cancel(request.user)
            messages.info(request, f"Transfer {transfer.transfer_number} cancelled.")
        except Exception as e:
            messages.error(request, str(e))
        
        return redirect('transfers:transfer_list')


@login_required
def get_batches_for_transfer(request):
    """API endpoint to get batches for a product at a specific location."""
    product_id = request.GET.get('product_id')
    location_id = request.GET.get('location_id')
    
    if not product_id or not location_id:
        return JsonResponse({'batches': []})
    
    batches = Batch.objects.filter(
        tenant=request.user.tenant,
        product_id=product_id,
        location_id=location_id,
        status='AVAILABLE',
        current_quantity__gt=0
    ).values('id', 'batch_number', 'current_quantity', 'unit_cost', 'expiry_date')
    
    return JsonResponse({'batches': list(batches)})
