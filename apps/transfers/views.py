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


class TransferListView(LoginRequiredMixin, ListView):
    """List all transfers for the tenant."""
    model = Transfer
    template_name = 'transfers/transfer_list.html'
    context_object_name = 'transfers'
    
    # Active statuses (still in progress)
    ACTIVE_STATUSES = ['DRAFT', 'SENT']
    # Completed statuses (finished their cycle)
    COMPLETED_STATUSES = ['RECEIVED', 'PARTIAL', 'DISPUTED', 'CLOSED', 'CANCELLED']
    
    def get_base_queryset(self):
        """Get base queryset with tenant and permission filtering."""
        from django.db.models import Q
        user = self.request.user
        queryset = Transfer.objects.filter(tenant=user.tenant)
        
        # Non-admin users only see transfers they're involved in
        if not (user.role and user.role.name == 'ADMIN'):
            role_location_map = {
                'PRODUCTION_MANAGER': 'PRODUCTION',
                'STORES_MANAGER': 'STORES',
                'SHOP_MANAGER': 'SHOP',
            }
            user_location_type = role_location_map.get(user.role.name if user.role else None)
            
            # Build filter: user's specific location OR matching location type
            location_filter = Q()
            if user.location:
                location_filter |= Q(source_location=user.location) | Q(destination_location=user.location)
            if user_location_type:
                location_filter |= Q(source_location__location_type=user_location_type) | Q(destination_location__location_type=user_location_type)
            
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
        return base_queryset.filter(status__in=self.ACTIVE_STATUSES)
    
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
        ).order_by('-received_at', '-created_at')
        
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
                'field': form[f'received_{item.pk}']
            })
        
        return render(request, self.template_name, {
            'transfer': transfer,
            'form': form,
            'items_with_fields': items_with_fields
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
        
        # Apply role-based filtering (same logic as TransferListView)
        if not (user.role and user.role.name == 'ADMIN'):
            role_location_map = {
                'PRODUCTION_MANAGER': 'PRODUCTION',
                'STORES_MANAGER': 'STORES',
                'SHOP_MANAGER': 'SHOP',
            }
            user_location_type = role_location_map.get(user.role.name if user.role else None)
            
            location_filter = Q()
            if user.location:
                location_filter |= Q(transfer__source_location=user.location) | Q(transfer__destination_location=user.location)
            if user_location_type:
                location_filter |= Q(transfer__source_location__location_type=user_location_type) | Q(transfer__destination_location__location_type=user_location_type)
            
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


class StockRequestListView(LoginRequiredMixin, PaginationMixin, ListView):
    """List stock requests for the tenant."""
    model = StockRequest
    template_name = 'transfers/stock_request_list.html'
    context_object_name = 'requests'
    
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
        
        return queryset.select_related(
            'requesting_location', 'supplying_location', 'requested_by'
        )
    
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
                f"Please edit and send the transfer."
            )
            return redirect('transfers:transfer_detail', pk=transfer.pk)
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
