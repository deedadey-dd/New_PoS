"""
Views for inventory app.
Handles products, categories, batches, and stock management.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.views import View
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, DetailView
from django.urls import reverse_lazy
from django.db.models import Sum, F, Q
from django.http import JsonResponse
from decimal import Decimal

from .models import Category, Product, Batch, InventoryLedger, ShopPrice
from .forms import CategoryForm, ProductForm, BatchForm, StockAdjustmentForm, ShopPriceForm
from apps.core.models import Location


# ============ Category Views ============
class CategoryListView(LoginRequiredMixin, ListView):
    """List all categories for the tenant."""
    model = Category
    template_name = 'inventory/category_list.html'
    context_object_name = 'categories'
    
    def get_queryset(self):
        return Category.objects.filter(
            tenant=self.request.user.tenant
        ).select_related('parent')


class CategoryCreateView(LoginRequiredMixin, CreateView):
    """Create a new category."""
    model = Category
    form_class = CategoryForm
    template_name = 'inventory/category_form.html'
    success_url = reverse_lazy('inventory:category_list')
    
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['tenant'] = self.request.user.tenant
        return kwargs
    
    def form_valid(self, form):
        form.instance.tenant = self.request.user.tenant
        messages.success(self.request, f'Category "{form.instance.name}" created successfully!')
        return super().form_valid(form)


class CategoryUpdateView(LoginRequiredMixin, UpdateView):
    """Update an existing category."""
    model = Category
    form_class = CategoryForm
    template_name = 'inventory/category_form.html'
    success_url = reverse_lazy('inventory:category_list')
    
    def get_queryset(self):
        return Category.objects.filter(tenant=self.request.user.tenant)
    
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['tenant'] = self.request.user.tenant
        return kwargs
    
    def form_valid(self, form):
        messages.success(self.request, f'Category "{form.instance.name}" updated!')
        return super().form_valid(form)


class CategoryDeleteView(LoginRequiredMixin, DeleteView):
    """Delete a category."""
    model = Category
    template_name = 'inventory/category_confirm_delete.html'
    success_url = reverse_lazy('inventory:category_list')
    
    def get_queryset(self):
        return Category.objects.filter(tenant=self.request.user.tenant)


# ============ Product Views ============
class ProductListView(LoginRequiredMixin, ListView):
    """List all products for the tenant."""
    model = Product
    template_name = 'inventory/product_list.html'
    context_object_name = 'products'
    
    def get_queryset(self):
        queryset = Product.objects.filter(
            tenant=self.request.user.tenant
        ).select_related('category')
        
        # Search filter
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | 
                Q(sku__icontains=search) |
                Q(description__icontains=search)
            )
        
        # Category filter
        category = self.request.GET.get('category')
        if category:
            queryset = queryset.filter(category_id=category)
        
        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['categories'] = Category.objects.filter(
            tenant=self.request.user.tenant,
            is_active=True
        )
        return context


class ProductDetailView(LoginRequiredMixin, DetailView):
    """View product details including stock levels."""
    model = Product
    template_name = 'inventory/product_detail.html'
    context_object_name = 'product'
    
    def get_queryset(self):
        return Product.objects.filter(tenant=self.request.user.tenant)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        product = self.object
        
        # Get stock by location
        context['stock_by_location'] = InventoryLedger.objects.filter(
            product=product
        ).values(
            'location__name', 'location__location_type'
        ).annotate(
            total_stock=Sum('quantity')
        ).order_by('location__name')
        
        # Get active batches
        context['batches'] = Batch.objects.filter(
            product=product,
            status='AVAILABLE',
            current_quantity__gt=0
        ).select_related('location').order_by('expiry_date')
        
        # Get recent ledger entries
        context['recent_entries'] = InventoryLedger.objects.filter(
            product=product
        ).select_related('location', 'created_by')[:20]
        
        return context


class ProductCreateView(LoginRequiredMixin, CreateView):
    """Create a new product."""
    model = Product
    form_class = ProductForm
    template_name = 'inventory/product_form.html'
    success_url = reverse_lazy('inventory:product_list')
    
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['tenant'] = self.request.user.tenant
        return kwargs
    
    def form_valid(self, form):
        form.instance.tenant = self.request.user.tenant
        messages.success(self.request, f'Product "{form.instance.name}" created successfully!')
        return super().form_valid(form)


class ProductUpdateView(LoginRequiredMixin, UpdateView):
    """Update an existing product."""
    model = Product
    form_class = ProductForm
    template_name = 'inventory/product_form.html'
    success_url = reverse_lazy('inventory:product_list')
    
    def get_queryset(self):
        return Product.objects.filter(tenant=self.request.user.tenant)
    
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['tenant'] = self.request.user.tenant
        return kwargs
    
    def form_valid(self, form):
        messages.success(self.request, f'Product "{form.instance.name}" updated!')
        return super().form_valid(form)


class ProductDeleteView(LoginRequiredMixin, DeleteView):
    """Delete a product."""
    model = Product
    template_name = 'inventory/product_confirm_delete.html'
    success_url = reverse_lazy('inventory:product_list')
    
    def get_queryset(self):
        return Product.objects.filter(tenant=self.request.user.tenant)


# ============ Batch Views ============
class BatchListView(LoginRequiredMixin, ListView):
    """List all batches for the tenant."""
    model = Batch
    template_name = 'inventory/batch_list.html'
    context_object_name = 'batches'
    
    def get_queryset(self):
        queryset = Batch.objects.filter(
            tenant=self.request.user.tenant
        ).select_related('product', 'location')
        
        # Status filter
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        
        # Location filter
        location = self.request.GET.get('location')
        if location:
            queryset = queryset.filter(location_id=location)
        
        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['locations'] = Location.objects.filter(
            tenant=self.request.user.tenant,
            is_active=True
        )
        return context


class BatchCreateView(LoginRequiredMixin, CreateView):
    """Create a new batch (receive stock)."""
    model = Batch
    form_class = BatchForm
    template_name = 'inventory/batch_form.html'
    success_url = reverse_lazy('inventory:batch_list')
    
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['tenant'] = self.request.user.tenant
        return kwargs
    
    def form_valid(self, form):
        form.instance.tenant = self.request.user.tenant
        form.instance.current_quantity = form.instance.initial_quantity
        
        # Save the batch first
        response = super().form_valid(form)
        
        # Create ledger entry for stock in
        InventoryLedger.objects.create(
            tenant=self.request.user.tenant,
            product=self.object.product,
            batch=self.object,
            location=self.object.location,
            transaction_type='IN',
            quantity=self.object.initial_quantity,
            unit_cost=self.object.unit_cost,
            reference_type='Batch',
            reference_id=self.object.pk,
            notes=f"Initial stock receipt - Batch {self.object.batch_number}",
            created_by=self.request.user
        )
        
        messages.success(self.request, f'Batch "{self.object.batch_number}" received successfully!')
        return response


class BatchDetailView(LoginRequiredMixin, DetailView):
    """View batch details."""
    model = Batch
    template_name = 'inventory/batch_detail.html'
    context_object_name = 'batch'
    
    def get_queryset(self):
        return Batch.objects.filter(tenant=self.request.user.tenant)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['ledger_entries'] = InventoryLedger.objects.filter(
            batch=self.object
        ).select_related('created_by').order_by('-created_at')
        return context


# ============ Stock Views ============
class StockOverviewView(LoginRequiredMixin, View):
    """Overview of stock across all locations."""
    template_name = 'inventory/stock_overview.html'
    
    def get(self, request):
        tenant = request.user.tenant
        
        # Stock by product and location
        stock_summary = InventoryLedger.objects.filter(
            tenant=tenant
        ).values(
            'product__id', 'product__name', 'product__sku',
            'location__id', 'location__name', 'location__location_type'
        ).annotate(
            total_stock=Sum('quantity')
        ).filter(total_stock__gt=0).order_by('product__name', 'location__name')
        
        # Low stock alerts
        low_stock = []
        products = Product.objects.filter(tenant=tenant, is_active=True)
        for product in products:
            total = product.get_total_stock()
            if total <= product.reorder_level:
                low_stock.append({
                    'product': product,
                    'current_stock': total,
                    'reorder_level': product.reorder_level
                })
        
        # Expiring soon (within 30 days)
        from django.utils import timezone
        from datetime import timedelta
        expiring_batches = Batch.objects.filter(
            tenant=tenant,
            status='AVAILABLE',
            current_quantity__gt=0,
            expiry_date__lte=timezone.now().date() + timedelta(days=30),
            expiry_date__gte=timezone.now().date()
        ).select_related('product', 'location').order_by('expiry_date')
        
        context = {
            'stock_summary': stock_summary,
            'low_stock': low_stock,
            'expiring_batches': expiring_batches,
        }
        
        return render(request, self.template_name, context)


class StockAdjustmentView(LoginRequiredMixin, View):
    """Create stock adjustments."""
    template_name = 'inventory/stock_adjustment.html'
    
    def get(self, request):
        form = StockAdjustmentForm(tenant=request.user.tenant)
        return render(request, self.template_name, {'form': form})
    
    def post(self, request):
        form = StockAdjustmentForm(request.POST, tenant=request.user.tenant)
        if form.is_valid():
            product = form.cleaned_data['product']
            location = form.cleaned_data['location']
            batch = form.cleaned_data.get('batch')
            quantity = form.cleaned_data['quantity']
            adjustment_type = form.cleaned_data['adjustment_type']
            reason = form.cleaned_data['reason']
            
            # Create ledger entry
            InventoryLedger.objects.create(
                tenant=request.user.tenant,
                product=product,
                batch=batch,
                location=location,
                transaction_type=adjustment_type,
                quantity=quantity,
                unit_cost=batch.unit_cost if batch else None,
                notes=reason,
                created_by=request.user
            )
            
            messages.success(request, f'Stock adjustment recorded for {product.name}.')
            return redirect('inventory:stock_overview')
        
        return render(request, self.template_name, {'form': form})


# ============ API Views ============
def get_batches_for_product(request):
    """AJAX endpoint to get batches for a product at a location."""
    product_id = request.GET.get('product_id')
    location_id = request.GET.get('location_id')
    
    if not product_id or not location_id:
        return JsonResponse({'batches': []})
    
    batches = Batch.objects.filter(
        product_id=product_id,
        location_id=location_id,
        status='AVAILABLE',
        current_quantity__gt=0,
        tenant=request.user.tenant
    ).values('id', 'batch_number', 'current_quantity', 'unit_cost', 'expiry_date')
    
    return JsonResponse({'batches': list(batches)})
