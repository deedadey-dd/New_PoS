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
