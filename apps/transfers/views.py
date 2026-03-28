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
from django.db import transaction, models

from .models import Transfer, TransferItem
from .forms import TransferForm, TransferItemFormSet, TransferItemForm, TransferReceiveForm, TransferDisputeForm, TransferCloseForm
from django.forms import inlineformset_factory
from apps.core.decorators import role_required
from apps.inventory.models import Batch, Product
from apps.core.mixins import PaginationMixin


from apps.core.mixins import SortableMixin

class TransferListView(LoginRequiredMixin, SortableMixin, ListView):
    """List all transfers for the tenant."""
    model = Transfer
    template_name = 'transfers/transfer_list.html'
    context_object_name = 'transfers'
    sortable_fields = ['created_at', 'transfer_number', 'source_location__name', 'destination_location__name', 'status']
    default_sort = '-created_at'
    
    # Active statuses (still in progress)
    ACTIVE_STATUSES = ['DRAFT', 'SENT']
    # Completed statuses (finished their cycle)
    COMPLETED_STATUSES = ['RECEIVED', 'PARTIAL', 'DISPUTED', 'CLOSED', 'CANCELLED']
    
    def get_base_queryset(self):
        """Get base queryset with tenant and permission filtering."""
        from django.db.models import Q
        user = self.request.user
        queryset = Transfer.objects.filter(tenant=user.tenant)
        
        # Role-based visibility filtering
        role_name = user.role.name if user.role else None
        
        # Roles that see ALL tenant transfers (no location filtering)
        full_access_roles = {'ADMIN', 'STORES_MANAGER', 'AUDITOR', 'ACCOUNTANT'}
        
        if role_name not in full_access_roles:
            # Shop roles and others: only see transfers involving their specific location
            if user.location:
                location_filter = (
                    Q(source_location=user.location) | Q(destination_location=user.location)
                )
                queryset = queryset.filter(location_filter)
        
        # Filter by location (either source or destination)
        location = self.request.GET.get('location')
        if location:
            queryset = queryset.filter(
                Q(source_location_id=location) | Q(destination_location_id=location)
            )
        
        return queryset.select_related(
            'source_location', 'destination_location', 'created_by'
        )
    
    def get_queryset(self):
        from django.db.models import Q
        base_queryset = self.get_base_queryset()
        
        # Filter by status if specified
        status = self.request.GET.get('status')
        if status:
            return base_queryset.filter(status=status)
        
        # By default, show active transfers in main list
        return self.apply_sorting(base_queryset.filter(status__in=self.ACTIVE_STATUSES))
    
    def get_context_data(self, **kwargs):
        from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
        from apps.core.models import Location
        
        context = super().get_context_data(**kwargs)
        context['locations'] = Location.objects.filter(
            tenant=self.request.user.tenant, is_active=True
        )
        context['status_choices'] = Transfer.STATUS_CHOICES
        
        # Get completed transfers (history) with pagination
        history_queryset = self.get_base_queryset().filter(
            status__in=self.COMPLETED_STATUSES
        )
        history_queryset = self.apply_sorting(history_queryset)
        
        # Pagination for history
        history_page = self.request.GET.get('history_page', 1)
        paginator = Paginator(history_queryset, 10)  # 10 items per page
        
        try:
            history_transfers = paginator.page(history_page)
        except PageNotAnInteger:
            history_transfers = paginator.page(1)
        except EmptyPage:
            history_transfers = paginator.page(paginator.num_pages)
        
        context['history_transfers'] = history_transfers
        context['history_paginator'] = paginator
        
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
        
        # Determine source type for conditional display
        source_type = None
        if form.source_location_display:
            source_type = form.source_location_display.location_type
        
        return render(request, self.template_name, {
            'form': form,
            'formset': formset,
            'title': 'Create Transfer',
            'source_type': source_type,
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
            'title': 'Create Transfer',
            'source_type': form.cleaned_data.get('source_location').location_type if form.cleaned_data.get('source_location') else None,
        })


class TransferEditView(LoginRequiredMixin, View):
    """Edit an existing draft transfer."""
    template_name = 'transfers/transfer_edit.html'

    def get(self, request, pk):
        transfer = get_object_or_404(
            Transfer, pk=pk, tenant=request.user.tenant
        )

        # Only DRAFT transfers can be edited
        if transfer.status != 'DRAFT':
            messages.error(request, f"Cannot edit transfer in {transfer.get_status_display()} status.")
            return redirect('transfers:transfer_detail', pk=pk)

        # Only source users can edit
        if not transfer.user_is_source(request.user):
            messages.error(request, "You don't have permission to edit this transfer.")
            return redirect('transfers:transfer_detail', pk=pk)

        form = TransferForm(
            instance=transfer,
            tenant=request.user.tenant,
        )
        # Set source_location_display for the template
        form.source_location_display = transfer.source_location

        # Build formset bound to the existing items, plus 1 extra empty row
        EditTransferItemFormSet = inlineformset_factory(
            Transfer,
            TransferItem,
            form=TransferItemForm,
            extra=1,
            can_delete=True,
            min_num=0,
            validate_min=False,
        )
        formset = EditTransferItemFormSet(
            instance=transfer,
            queryset=transfer.items.all(),
        )

        # Filter products and batches for each form in formset
        for item_form in formset:
            if hasattr(item_form.fields.get('product'), 'queryset'):
                item_form.fields['product'].queryset = Product.objects.filter(
                    tenant=request.user.tenant, is_active=True
                )
            # For existing items, populate batch queryset with product-specific batches
            if item_form.instance and item_form.instance.pk and item_form.instance.product:
                item_form.fields['batch'].queryset = Batch.objects.filter(
                    tenant=request.user.tenant,
                    product=item_form.instance.product,
                    location=transfer.source_location,
                    status='AVAILABLE',
                    current_quantity__gt=0
                ).order_by('-id')
                # Also include the currently selected batch even if it has 0 quantity
                if item_form.instance.batch:
                    item_form.fields['batch'].queryset = (
                        item_form.fields['batch'].queryset | Batch.objects.filter(pk=item_form.instance.batch.pk)
                    ).distinct()
            else:
                item_form.fields['batch'].queryset = Batch.objects.none()

        source_type = transfer.source_location.location_type

        # Check if this transfer came from a stock request
        source_request = transfer.source_request.first() if hasattr(transfer, 'source_request') else None

        return render(request, self.template_name, {
            'form': form,
            'formset': formset,
            'transfer': transfer,
            'title': f'Edit Transfer {transfer.transfer_number}',
            'source_type': source_type,
            'source_request': source_request,
        })

    def post(self, request, pk):
        transfer = get_object_or_404(
            Transfer, pk=pk, tenant=request.user.tenant
        )

        if transfer.status != 'DRAFT':
            messages.error(request, f"Cannot edit transfer in {transfer.get_status_display()} status.")
            return redirect('transfers:transfer_detail', pk=pk)

        if not transfer.user_is_source(request.user):
            messages.error(request, "You don't have permission to edit this transfer.")
            return redirect('transfers:transfer_detail', pk=pk)

        form = TransferForm(request.POST, instance=transfer, tenant=request.user.tenant)

        EditTransferItemFormSet = inlineformset_factory(
            Transfer,
            TransferItem,
            form=TransferItemForm,
            extra=1,
            can_delete=True,
            min_num=0,
            validate_min=False,
        )
        formset = EditTransferItemFormSet(request.POST, instance=transfer)

        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                transfer = form.save()

                for item_form in formset:
                    if item_form.cleaned_data:
                        if item_form.cleaned_data.get('DELETE', False):
                            if item_form.instance.pk:
                                item_form.instance.delete()
                        else:
                            item = item_form.save(commit=False)
                            item.tenant = request.user.tenant
                            item.transfer = transfer
                            item.save()

                messages.success(request, f"Transfer {transfer.transfer_number} updated successfully!")
                return redirect('transfers:transfer_detail', pk=transfer.pk)

        # Re-render with errors
        form.source_location_display = transfer.source_location
        source_type = transfer.source_location.location_type
        source_request = transfer.source_request.first() if hasattr(transfer, 'source_request') else None

        return render(request, self.template_name, {
            'form': form,
            'formset': formset,
            'transfer': transfer,
            'title': f'Edit Transfer {transfer.transfer_number}',
            'source_type': source_type,
            'source_request': source_request,
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
    
    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        # Check if user can view this transfer
        if not self.object.user_can_view(request.user):
            messages.error(request, "You don't have permission to view this transfer.")
            return redirect('transfers:transfer_list')
        return super().get(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        transfer = self.object
        # Pass permission flags to template
        context['can_send'] = transfer.user_can_send(user)
        context['can_receive'] = transfer.user_can_receive(user)
        context['can_cancel'] = transfer.user_can_cancel(user)
        context['can_edit'] = transfer.status == 'DRAFT' and transfer.user_is_source(user)
        context['is_source'] = transfer.user_is_source(user)
        context['is_destination'] = transfer.user_is_destination(user)
        return context


class TransferSendView(LoginRequiredMixin, View):
    """Send a draft transfer."""
    
    def post(self, request, pk):
        transfer = get_object_or_404(
            Transfer, pk=pk, tenant=request.user.tenant
        )
        
        # Check permission
        if not transfer.user_can_send(request.user):
            messages.error(request, "You don't have permission to send this transfer.")
            return redirect('transfers:transfer_detail', pk=pk)
        
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
        
        # Check permission - only destination users can receive
        if not transfer.user_can_receive(request.user):
            messages.error(request, "You don't have permission to receive this transfer.")
            return redirect('transfers:transfer_detail', pk=pk)
        
        if not transfer.can_receive:
            messages.error(request, f"Cannot receive transfer in {transfer.status} status.")
            return redirect('transfers:transfer_detail', pk=pk)
        
        form = TransferReceiveForm(transfer=transfer)
        
        # Attach form fields to items for easy template access
        items_with_fields = []
        for item in transfer.items.all():
            items_with_fields.append({
                'item': item,
                'field': form[f'received_{item.pk}'],
                'reason_field': form[f'reason_{item.pk}'],
                'action_field': form[f'action_{item.pk}'],
                'notes_field': form[f'notes_{item.pk}'],
            })
        
        return render(request, self.template_name, {
            'transfer': transfer,
            'form': form,
            'items_with_fields': items_with_fields,
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
            # Build items_received dict and discrepancy_data
            items_received = {}
            discrepancy_data = {}
            for item in transfer.items.all():
                items_received[str(item.pk)] = form.cleaned_data.get(f'received_{item.pk}', 0)
                reason = form.cleaned_data.get(f'reason_{item.pk}', '')
                notes = form.cleaned_data.get(f'notes_{item.pk}', '')
                action = form.cleaned_data.get(f'action_{item.pk}', 'RETURN')
                if reason:
                    discrepancy_data[str(item.pk)] = {
                        'reason': reason,
                        'notes': notes,
                        'action': action,
                    }
            
            try:
                with transaction.atomic():
                    transfer.receive(request.user, items_received, discrepancy_data)
                
                if transfer.status == 'PARTIAL':
                    messages.success(request, f"Transfer {transfer.transfer_number} partially received.")
                else:
                    messages.success(request, f"Transfer {transfer.transfer_number} received!")
            except Exception as e:
                messages.error(request, str(e))
        else:
            # Re-render with errors
            items_with_fields = []
            for item in transfer.items.all():
                items_with_fields.append({
                    'item': item,
                    'field': form[f'received_{item.pk}'],
                    'reason_field': form[f'reason_{item.pk}'],
                    'action_field': form[f'action_{item.pk}'],
                    'notes_field': form[f'notes_{item.pk}'],
                })
            return render(request, self.template_name, {
                'transfer': transfer,
                'form': form,
                'items_with_fields': items_with_fields,
            })
        
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
    ).order_by('-id').values('id', 'batch_number', 'current_quantity', 'unit_cost', 'expiry_date')
    
    return JsonResponse({'batches': list(batches)})


@login_required
def get_batch_details(request):
    """API endpoint to get batch details for unit cost auto-fill."""
    batch_id = request.GET.get('batch_id')
    
    if not batch_id:
        return JsonResponse({'error': 'batch_id required'}, status=400)
    
    try:
        batch = Batch.objects.get(
            pk=batch_id,
            tenant=request.user.tenant
        )
        return JsonResponse({
            'id': batch.pk,
            'batch_number': batch.batch_number,
            'unit_cost': str(batch.unit_cost) if batch.unit_cost else None,
            'current_quantity': str(batch.current_quantity),
        })
    except Batch.DoesNotExist:
        return JsonResponse({'error': 'Batch not found'}, status=404)


class TransferItemHistoryView(LoginRequiredMixin, ListView):
    """
    Product-centric view of transfers showing individual items 
    with their transfer metadata (number, date, locations, status).
    """
    model = TransferItem
    template_name = 'transfers/transfer_item_history.html'
    context_object_name = 'transfer_items'
    paginate_by = 25
    
    def get_queryset(self):
        from django.db.models import Q
        from datetime import datetime, timedelta
        
        user = self.request.user
        queryset = TransferItem.objects.filter(
            tenant=user.tenant,
            transfer__status__in=['SENT', 'RECEIVED', 'PARTIAL', 'DISPUTED', 'CLOSED']
        ).select_related(
            'product',
            'product__category',
            'batch',
            'transfer',
            'transfer__source_location',
            'transfer__destination_location',
        ).order_by('-transfer__created_at')
        
        # Role-based filtering (same logic as TransferListView)
        role_name = user.role.name if user.role else None
        full_access_roles = {'ADMIN', 'STORES_MANAGER', 'AUDITOR', 'ACCOUNTANT'}
        
        if role_name not in full_access_roles:
            # Shop roles and others: only see transfers involving their specific location
            if user.location:
                location_filter = (
                    Q(transfer__source_location=user.location) | Q(transfer__destination_location=user.location)
                )
                queryset = queryset.filter(location_filter)
        
        # Filter by product search
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(product__name__icontains=search) |
                Q(product__sku__icontains=search) |
                Q(transfer__transfer_number__icontains=search)
            )
        
        # Filter by location
        location = self.request.GET.get('location')
        if location:
            queryset = queryset.filter(
                Q(transfer__source_location_id=location) |
                Q(transfer__destination_location_id=location)
            )
        
        # Filter by direction (incoming/outgoing relative to user's location)
        direction = self.request.GET.get('direction')
        if direction and user.location:
            if direction == 'incoming':
                queryset = queryset.filter(transfer__destination_location=user.location)
            elif direction == 'outgoing':
                queryset = queryset.filter(transfer__source_location=user.location)
        
        # Filter by date range
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        if date_from:
            queryset = queryset.filter(transfer__created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(transfer__created_at__date__lte=date_to)
        
        # Filter by status
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(transfer__status=status)
        
        return queryset
    
    def get_context_data(self, **kwargs):
        from apps.core.models import Location
        
        context = super().get_context_data(**kwargs)
        context['locations'] = Location.objects.filter(
            tenant=self.request.user.tenant, is_active=True
        )
        context['status_choices'] = [
            ('SENT', 'Sent'),
            ('RECEIVED', 'Received'),
            ('PARTIAL', 'Partial'),
            ('DISPUTED', 'Disputed'),
            ('CLOSED', 'Closed'),
        ]
        context['user_location'] = self.request.user.location
        
        # Preserve filter values for form
        context['current_search'] = self.request.GET.get('search', '')
        context['current_location'] = self.request.GET.get('location', '')
        context['current_direction'] = self.request.GET.get('direction', '')
        context['current_status'] = self.request.GET.get('status', '')
        context['current_date_from'] = self.request.GET.get('date_from', '')
        context['current_date_to'] = self.request.GET.get('date_to', '')
        
        return context

# ==================== Stock Request Views ====================

from .models import StockRequest, StockRequestItem
from .forms import StockRequestForm, StockRequestItemForm, StockRequestItemFormSet, StockRequestRejectForm


class StockRequestListView(LoginRequiredMixin, SortableMixin, ListView):
    """List stock requests for the tenant."""
    model = StockRequest
    template_name = 'transfers/stock_request_list.html'
    context_object_name = 'requests'
    sortable_fields = ['created_at', 'request_number', 'requesting_location__name', 'supplying_location__name', 'status']
    default_sort = '-created_at'
    
    def get_queryset(self):
        from django.db.models import Q
        user = self.request.user
        queryset = StockRequest.objects.filter(tenant=user.tenant)
        
        # Get tab filter
        tab = self.request.GET.get('tab', 'incoming')
        
        # Role-based filtering
        if user.role and user.role.name != 'ADMIN':
            role_location_map = {
                'PRODUCTION_MANAGER': 'PRODUCTION',
                'STORES_MANAGER': 'STORES',
                'SHOP_MANAGER': 'SHOP',
            }
            user_location_type = role_location_map.get(user.role.name)
            
            if tab == 'outgoing':
                # Requests sent by user's location type
                if user.location:
                    queryset = queryset.filter(requesting_location=user.location)
                elif user_location_type:
                    queryset = queryset.filter(requesting_location__location_type=user_location_type)
            else:  # incoming
                # Requests received by user's location type
                if user.location:
                    queryset = queryset.filter(supplying_location=user.location)
                elif user_location_type:
                    queryset = queryset.filter(supplying_location__location_type=user_location_type)
        
        # Filter by status if specified
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        
        return self.apply_sorting(queryset.select_related(
            'requesting_location', 'supplying_location', 'requested_by'
        ))
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_choices'] = StockRequest.STATUS_CHOICES
        context['current_tab'] = self.request.GET.get('tab', 'incoming')
        
        # Count for tabs
        user = self.request.user
        base_qs = StockRequest.objects.filter(tenant=user.tenant)
        
        if user.role and user.role.name != 'ADMIN':
            role_location_map = {
                'PRODUCTION_MANAGER': 'PRODUCTION',
                'STORES_MANAGER': 'STORES',
                'SHOP_MANAGER': 'SHOP',
            }
            user_location_type = role_location_map.get(user.role.name)
            
            if user.location:
                context['outgoing_count'] = base_qs.filter(requesting_location=user.location).count()
                context['incoming_count'] = base_qs.filter(supplying_location=user.location, status='PENDING').count()
            elif user_location_type:
                context['outgoing_count'] = base_qs.filter(requesting_location__location_type=user_location_type).count()
                context['incoming_count'] = base_qs.filter(supplying_location__location_type=user_location_type, status='PENDING').count()
        else:
            context['outgoing_count'] = base_qs.count()
            context['incoming_count'] = base_qs.filter(status='PENDING').count()
        
        return context


class StockRequestCreateView(LoginRequiredMixin, View):
    """Create a new stock request."""
    template_name = 'transfers/stock_request_form.html'
    
    def get(self, request):
        form = StockRequestForm(tenant=request.user.tenant, user=request.user)
        
        # Get user's location for stock check
        user_location = request.user.location
        if not user_location and request.user.role:
            role_location_map = {
                'PRODUCTION_MANAGER': 'PRODUCTION',
                'STORES_MANAGER': 'STORES',
                'SHOP_MANAGER': 'SHOP',
            }
            location_type = role_location_map.get(request.user.role.name)
            if location_type:
                from apps.core.models import Location
                user_location = Location.objects.filter(
                    tenant=request.user.tenant,
                    is_active=True,
                    location_type=location_type
                ).first()
        
        # Get low stock products at user's location (match Stock Alerts logic)
        low_stock_products = []
        if user_location:
            # Get products with reorder level set
            products = Product.objects.filter(
                tenant=request.user.tenant,
                is_active=True,
                reorder_level__gt=0
            )
            
            for product in products:
                stock_qty = product.get_stock_at_location(user_location)
                
                if stock_qty <= product.reorder_level:
                    reorder_qty = max(product.reorder_level - stock_qty, 1)
                    low_stock_products.append({
                        'product': product.pk,
                        'quantity_requested': reorder_qty,
                        'notes': f'Current: {int(stock_qty)}, Reorder: {int(product.reorder_level)}'
                    })
            
            # Sort by stock (lowest first) and limit to 20
            low_stock_products.sort(key=lambda x: x.get('notes', ''))
            low_stock_products = low_stock_products[:20]
        
        # Create formset with preloaded low stock items
        num_preloaded = len(low_stock_products)
        extra_forms = max(1, 1 if num_preloaded == 0 else 0)  # At least 1 empty form if no preloaded
        
        RequestItemFormSetWithExtra = inlineformset_factory(
            StockRequest,
            StockRequestItem,
            form=StockRequestItemForm,
            extra=num_preloaded + extra_forms,
            can_delete=True,
            min_num=0,
            validate_min=False,
        )
        
        # Create initial data for formset
        initial_data = low_stock_products if low_stock_products else []
        formset = RequestItemFormSetWithExtra(
            queryset=StockRequestItem.objects.none(),
            initial=initial_data
        )
        
        # Filter products for each form
        for item_form in formset:
            if hasattr(item_form.fields.get('product'), 'queryset'):
                item_form.fields['product'].queryset = Product.objects.filter(
                    tenant=request.user.tenant, is_active=True
                )
        
        return render(request, self.template_name, {
            'form': form,
            'formset': formset,
            'title': 'Create Stock Request',
            'preloaded_count': num_preloaded,
        })
    
    def post(self, request):
        form = StockRequestForm(request.POST, tenant=request.user.tenant, user=request.user)
        formset = StockRequestItemFormSet(request.POST)
        
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                stock_request = form.save(commit=False)
                stock_request.tenant = request.user.tenant
                stock_request.requested_by = request.user
                stock_request.save()
                
                for item_form in formset:
                    if item_form.cleaned_data and not item_form.cleaned_data.get('DELETE', False):
                        item = item_form.save(commit=False)
                        item.tenant = request.user.tenant
                        item.request = stock_request
                        item.save()
                
                # Create notification for supplier
                stock_request._create_notification(
                    f"New Stock Request {stock_request.request_number}",
                    f"{stock_request.requesting_location.name} is requesting stock.",
                    'REQUEST_NEW',
                    stock_request.supplying_location
                )
                
                messages.success(request, f"Stock Request {stock_request.request_number} created successfully!")
                return redirect('transfers:stock_request_detail', pk=stock_request.pk)
        
        return render(request, self.template_name, {
            'form': form,
            'formset': formset,
            'title': 'Create Stock Request'
        })


class StockRequestDetailView(LoginRequiredMixin, DetailView):
    """View stock request details."""
    model = StockRequest
    template_name = 'transfers/stock_request_detail.html'
    context_object_name = 'request'
    
    def get_queryset(self):
        return StockRequest.objects.filter(
            tenant=self.request.user.tenant
        ).select_related(
            'requesting_location', 'supplying_location',
            'requested_by', 'approved_by', 'resulting_transfer'
        ).prefetch_related('items__product')
    
    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not self.object.user_can_view(request.user):
            messages.error(request, "You don't have permission to view this request.")
            return redirect('transfers:stock_request_list')
        return super().get(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        stock_request = self.object
        
        context['can_approve'] = stock_request.user_can_approve(user)
        context['can_reject'] = stock_request.user_can_reject(user)
        context['can_convert'] = stock_request.user_can_convert(user)
        context['can_cancel'] = stock_request.user_can_cancel(user)
        context['is_requestor'] = stock_request.user_is_requestor(user)
        context['is_supplier'] = stock_request.user_is_supplier(user)
        
        return context


class StockRequestApproveView(LoginRequiredMixin, View):
    """Approve a stock request."""
    
    def post(self, request, pk):
        stock_request = get_object_or_404(
            StockRequest, pk=pk, tenant=request.user.tenant
        )
        
        if not stock_request.user_can_approve(request.user):
            messages.error(request, "You don't have permission to approve this request.")
            return redirect('transfers:stock_request_detail', pk=pk)
        
        try:
            stock_request.approve(request.user)
            messages.success(request, f"Stock Request {stock_request.request_number} approved!")
        except Exception as e:
            messages.error(request, str(e))
        
        return redirect('transfers:stock_request_detail', pk=pk)


class StockRequestRejectView(LoginRequiredMixin, View):
    """Reject a stock request."""
    template_name = 'transfers/stock_request_reject.html'
    
    def get(self, request, pk):
        stock_request = get_object_or_404(
            StockRequest, pk=pk, tenant=request.user.tenant
        )
        
        if not stock_request.user_can_reject(request.user):
            messages.error(request, "You don't have permission to reject this request.")
            return redirect('transfers:stock_request_detail', pk=pk)
        
        form = StockRequestRejectForm()
        return render(request, self.template_name, {
            'request_obj': stock_request,
            'form': form
        })
    
    def post(self, request, pk):
        stock_request = get_object_or_404(
            StockRequest, pk=pk, tenant=request.user.tenant
        )
        
        form = StockRequestRejectForm(request.POST)
        
        if form.is_valid():
            try:
                stock_request.reject(request.user, form.cleaned_data['rejection_reason'])
                messages.warning(request, f"Stock Request {stock_request.request_number} rejected.")
            except Exception as e:
                messages.error(request, str(e))
        
        return redirect('transfers:stock_request_detail', pk=pk)


class StockRequestConvertView(LoginRequiredMixin, View):
    """Convert an approved stock request to a draft transfer."""
    
    def post(self, request, pk):
        stock_request = get_object_or_404(
            StockRequest, pk=pk, tenant=request.user.tenant
        )
        
        if not stock_request.user_can_convert(request.user):
            messages.error(request, "You don't have permission to convert this request.")
            return redirect('transfers:stock_request_detail', pk=pk)
        
        try:
            with transaction.atomic():
                transfer = stock_request.convert_to_transfer(request.user)
            messages.success(
                request, 
                f"Stock Request converted to Transfer {transfer.transfer_number}. "
                f"Please review, edit, and send the transfer."
            )
            return redirect('transfers:transfer_edit', pk=transfer.pk)
        except Exception as e:
            messages.error(request, str(e))
            return redirect('transfers:stock_request_detail', pk=pk)


class StockRequestCancelView(LoginRequiredMixin, View):
    """Cancel a pending stock request."""
    
    def post(self, request, pk):
        stock_request = get_object_or_404(
            StockRequest, pk=pk, tenant=request.user.tenant
        )
        
        if not stock_request.user_can_cancel(request.user):
            messages.error(request, "You don't have permission to cancel this request.")
            return redirect('transfers:stock_request_detail', pk=pk)
        
        try:
            stock_request.cancel(request.user)
            messages.info(request, f"Stock Request {stock_request.request_number} cancelled.")
        except Exception as e:
            messages.error(request, str(e))
        
        return redirect('transfers:stock_request_list')


# ==================== Stock Write-Off Views ====================

from .models import StockWriteOff
from .forms import StockWriteOffForm


class StockWriteOffListView(LoginRequiredMixin, SortableMixin, ListView):
    """List stock write-offs."""
    model = StockWriteOff
    template_name = 'transfers/write_off_list.html'
    context_object_name = 'write_offs'
    sortable_fields = ['created_at', 'writeoff_number', 'product__name', 'location__name', 'quantity', 'reason']
    default_sort = '-created_at'

    def get_queryset(self):
        from django.db.models import Q
        user = self.request.user
        queryset = StockWriteOff.objects.filter(
            tenant=user.tenant
        ).select_related('product', 'batch', 'location', 'performed_by')

        # Non-admin: only see write-offs at own location
        if user.role and user.role.name != 'ADMIN':
            if user.location:
                queryset = queryset.filter(location=user.location)
            else:
                role_location_map = {
                    'STORES_MANAGER': 'STORES',
                }
                loc_type = role_location_map.get(user.role.name)
                if loc_type:
                    queryset = queryset.filter(location__location_type=loc_type)

        # Search filter
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(product__name__icontains=search) |
                Q(product__sku__icontains=search) |
                Q(writeoff_number__icontains=search)
            )

        # Reason filter
        reason = self.request.GET.get('reason')
        if reason:
            queryset = queryset.filter(reason=reason)

        # Date range
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)

        return self.apply_sorting(queryset)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['reason_choices'] = StockWriteOff.REASON_CHOICES
        context['current_search'] = self.request.GET.get('search', '')
        context['current_reason'] = self.request.GET.get('reason', '')
        context['current_date_from'] = self.request.GET.get('date_from', '')
        context['current_date_to'] = self.request.GET.get('date_to', '')
        return context


class StockWriteOffCreateView(LoginRequiredMixin, View):
    """Create a stock write-off."""
    template_name = 'transfers/write_off_form.html'

    def _get_user_location(self, user):
        """Resolve the stores location for the user."""
        if user.location and user.location.location_type == 'STORES':
            return user.location
        # Fallback: find a stores location for this tenant
        from apps.core.models import Location
        return Location.objects.filter(
            tenant=user.tenant, is_active=True, location_type='STORES'
        ).first()

    def get(self, request):
        location = self._get_user_location(request.user)
        if not location:
            messages.error(request, "No stores location found. Write-offs can only be created at stores locations.")
            return redirect('transfers:write_off_list')

        form = StockWriteOffForm(tenant=request.user.tenant, location=location)
        return render(request, self.template_name, {
            'form': form,
            'location': location,
        })

    def post(self, request):
        location = self._get_user_location(request.user)
        if not location:
            messages.error(request, "No stores location found.")
            return redirect('transfers:write_off_list')

        form = StockWriteOffForm(request.POST, tenant=request.user.tenant, location=location)

        if form.is_valid():
            try:
                with transaction.atomic():
                    write_off = form.save(commit=False)
                    write_off.tenant = request.user.tenant
                    write_off.location = location
                    write_off.performed_by = request.user
                    write_off.save()
                messages.success(request, f"Write-off {write_off.writeoff_number} created. Stock deducted.")
                return redirect('transfers:write_off_list')
            except Exception as e:
                messages.error(request, str(e))

        return render(request, self.template_name, {
            'form': form,
            'location': location,
        })


# ============ Excel Export Views ============

class TransferListExportView(LoginRequiredMixin, View):
    """Export transfers list to Excel."""

    def get(self, request):
        from django.db.models import Q
        from apps.core.excel_utils import create_export_workbook, build_excel_response

        user = request.user
        queryset = Transfer.objects.filter(tenant=user.tenant)

        # Role-based filtering (same as TransferListView)
        role_name = user.role.name if user.role else None
        full_access_roles = {'ADMIN', 'STORES_MANAGER', 'AUDITOR', 'ACCOUNTANT'}

        if role_name not in full_access_roles:
            if user.location:
                location_filter = (
                    Q(source_location=user.location) | Q(destination_location=user.location)
                )
                queryset = queryset.filter(location_filter)

        # Location filter
        location_id = request.GET.get('location')
        if location_id:
            queryset = queryset.filter(
                Q(source_location_id=location_id) | Q(destination_location_id=location_id)
            )

        # Status filter
        status = request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)

        queryset = queryset.select_related(
            'source_location', 'destination_location', 'created_by'
        ).order_by('-created_at')

        headers = ['Transfer #', 'Date', 'Source', 'Destination', 'Status',
                    'Items', 'Created By', 'Sent At', 'Received At']
        rows = []
        for t in queryset:
            rows.append([
                t.transfer_number,
                t.created_at.strftime('%Y-%m-%d %H:%M') if t.created_at else '',
                t.source_location.name if t.source_location else '',
                t.destination_location.name if t.destination_location else '',
                t.get_status_display(),
                t.items.count(),
                t.created_by.get_full_name() or t.created_by.email if t.created_by else '',
                t.sent_at.strftime('%Y-%m-%d %H:%M') if t.sent_at else '',
                t.received_at.strftime('%Y-%m-%d %H:%M') if t.received_at else '',
            ])

        wb = create_export_workbook('Transfers', headers, rows)
        return build_excel_response(wb, 'transfers_export.xlsx')


class TransferItemHistoryExportView(LoginRequiredMixin, View):
    """Export transfer item history to Excel."""

    def get(self, request):
        from django.db.models import Q
        from apps.core.excel_utils import create_export_workbook, build_excel_response

        user = request.user
        queryset = TransferItem.objects.filter(
            tenant=user.tenant,
            transfer__status__in=['SENT', 'RECEIVED', 'PARTIAL', 'DISPUTED', 'CLOSED']
        ).select_related(
            'product', 'batch', 'transfer',
            'transfer__source_location', 'transfer__destination_location',
        ).order_by('-transfer__created_at')

        # Role-based filtering (same as TransferItemHistoryView)
        role_name = user.role.name if user.role else None
        full_access_roles = {'ADMIN', 'STORES_MANAGER', 'AUDITOR', 'ACCOUNTANT'}

        if role_name not in full_access_roles:
            if user.location:
                location_filter = (
                    Q(transfer__source_location=user.location) | Q(transfer__destination_location=user.location)
                )
                queryset = queryset.filter(location_filter)

        # Search filter
        search = request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(product__name__icontains=search) |
                Q(product__sku__icontains=search) |
                Q(transfer__transfer_number__icontains=search)
            )

        # Location filter
        location_id = request.GET.get('location')
        if location_id:
            queryset = queryset.filter(
                Q(transfer__source_location_id=location_id) |
                Q(transfer__destination_location_id=location_id)
            )

        # Direction filter
        direction = request.GET.get('direction')
        if direction and user.location:
            if direction == 'incoming':
                queryset = queryset.filter(transfer__destination_location=user.location)
            elif direction == 'outgoing':
                queryset = queryset.filter(transfer__source_location=user.location)

        # Date range
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')
        if date_from:
            queryset = queryset.filter(transfer__created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(transfer__created_at__date__lte=date_to)

        # Status filter
        status = request.GET.get('status')
        if status:
            queryset = queryset.filter(transfer__status=status)

        headers = ['Date', 'Transfer #', 'Product', 'SKU', 'Batch',
                    'Qty Sent', 'Qty Received', 'Source', 'Destination', 'Status']
        rows = []
        for item in queryset:
            rows.append([
                item.transfer.created_at.strftime('%Y-%m-%d') if item.transfer.created_at else '',
                item.transfer.transfer_number,
                item.product.name if item.product else '',
                item.product.sku if item.product else '',
                item.batch.batch_number if item.batch else '',
                float(item.quantity) if item.quantity else 0,
                float(item.received_quantity) if item.received_quantity is not None else '',
                item.transfer.source_location.name if item.transfer.source_location else '',
                item.transfer.destination_location.name if item.transfer.destination_location else '',
                item.transfer.get_status_display(),
            ])

        wb = create_export_workbook('Transfer Item History', headers, rows)
        return build_excel_response(wb, 'transfer_item_history_export.xlsx')
