from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

class PaginationMixin:
    """
    Mixin to handle dynamic pagination for both ListView and custom Views.
    Features:
    - Dynamic 'per_page' from URL parameter (default 25).
    - Options: 25, 50, 100.
    - Helper method `paginate_custom_queryset` for manual pagination.
    - Override `get_paginate_by` for Django GCBV ListView.
    """

    def get_per_page(self):
        """Get validated per_page value from request."""
        per_page = self.request.GET.get('per_page', 25)
        try:
            per_page = int(per_page)
            if per_page not in [25, 50, 100]:
                per_page = 25
        except (ValueError, TypeError):
            per_page = 25
        return per_page

    def get_paginate_by(self, queryset):
        """Override for Django Generic ListView."""
        return self.get_per_page()

    def get_context_data(self, **kwargs):
        """Inject per_page into context for ListView."""
        context = super().get_context_data(**kwargs)
        context['per_page'] = self.get_per_page()
        return context

    def paginate_custom_queryset(self, queryset):
        """
        Manually paginate a queryset (for non-ListViews).
        Returns: (page_obj, per_page)
        """
        per_page = self.get_per_page()
        paginator = Paginator(queryset, per_page)
        page = self.request.GET.get('page')
        
        try:
            page_obj = paginator.page(page)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)
            
        return page_obj, per_page

class SortableMixin(PaginationMixin):
    """
    Mixin to handle server-side sorting for views.
    Extends PaginationMixin to ensure sorting works with pagination.
    Supports sort_mappings for virtual or multi-column fields.
    """
    sortable_fields = []  # List of sortable field names
    default_sort = '-created_at'  # Default sort field
    sort_mappings = {}  # Map virtual sort keys to actual model fields/expressions

    def get_sort_params(self):
        """Extract sort parameters from the requests."""
        sort_by = self.request.GET.get('sort', None)
        direction = self.request.GET.get('dir', 'asc')

        # Clean sort_by and validate against sortable_fields or sort_mappings
        mappings = getattr(self, 'sort_mappings', {})
        if sort_by and (sort_by in self.sortable_fields or sort_by in mappings):
            if direction == 'desc':
                return f"-{sort_by}"
            return sort_by
        return self.default_sort

    def apply_sorting(self, queryset):
        """Apply sorting to the queryset."""
        import logging
        from django.db.models.functions import Lower
        from django.db.models import F
        from django.core.exceptions import FieldError

        logger = logging.getLogger(__name__)
        sort_expr = self.get_sort_params()
        is_desc = sort_expr.startswith('-')
        raw_field = sort_expr[1:] if is_desc else sort_expr

        # 1. Custom field mapping (e.g. attendant -> attendant__first_name, attendant__last_name)
        mappings = getattr(self, 'sort_mappings', {})
        if raw_field in mappings:
            target_fields = mappings[raw_field]
            if isinstance(target_fields, str):
                target_fields = [target_fields]
            order_args = []
            text_fields = ['name', 'sku', 'username', 'email', 'status', 'type', 'method', 'number']
            for tf in target_fields:
                is_tf_desc = is_desc
                clean_tf = tf
                if clean_tf.startswith('-'):
                    is_tf_desc = not is_desc
                    clean_tf = clean_tf[1:]
                if any(f in clean_tf.lower() for f in text_fields):
                    expr = Lower(clean_tf)
                    order_args.append(expr.desc(nulls_last=True) if is_tf_desc else expr.asc(nulls_last=True))
                else:
                    order_args.append(F(clean_tf).desc(nulls_last=True) if is_tf_desc else F(clean_tf).asc(nulls_last=True))
            try:
                return queryset.order_by(*order_args)
            except (FieldError, Exception) as e:
                logger.warning("Error applying mapped sort for '%s': %s. Falling back to default %s", raw_field, e, self.default_sort)
                return queryset.order_by(self.default_sort)

        # 2. Case-insensitive sorting for text fields
        text_fields = ['name', 'sku', 'username', 'email', 'status', 'type', 'method', 'number']
        is_text_sort = any(f in sort_expr.lower() for f in text_fields)

        try:
            if is_text_sort:
                if is_desc:
                    return queryset.order_by(Lower(raw_field).desc(nulls_last=True))
                else:
                    return queryset.order_by(Lower(sort_expr).asc(nulls_last=True))

            # For non-text numeric/date fields
            if is_desc:
                return queryset.order_by(F(raw_field).desc(nulls_last=True))
            else:
                return queryset.order_by(F(sort_expr).asc(nulls_last=True))
        except (FieldError, Exception) as e:
            logger.warning("Invalid sort expression '%s': %s. Falling back to default '%s'", sort_expr, e, self.default_sort)
            try:
                return queryset.order_by(self.default_sort)
            except Exception:
                return queryset

    def get_context_data(self, **kwargs):
        """Add sorting variables to context."""
        context = super().get_context_data(**kwargs)
        sort_by = self.request.GET.get('sort', '')
        direction = self.request.GET.get('dir', 'asc')
        context['current_sort'] = sort_by
        context['current_dir'] = direction
        return context
