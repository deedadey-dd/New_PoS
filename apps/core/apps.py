"""
Core app configuration.
"""
from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.core'
    verbose_name = 'Core'
    
    def ready(self):
        from django.db.models.signals import post_migrate
        post_migrate.connect(_sync_site_domain, sender=self)


def _sync_site_domain(sender, **kwargs):
    """Auto-sync Site #1 domain for sitemaps after migration."""
    try:
        from django.contrib.sites.models import Site
        from django.conf import settings
        from urllib.parse import urlparse
        frontend_url = getattr(settings, 'FRONTEND_URL', 'https://pos.hendaxis.com')
        domain = urlparse(frontend_url).netloc or 'pos.hendaxis.com'
        Site.objects.filter(id=1).update(
            domain=domain,
            name=getattr(settings, 'PLATFORM_COMPANY_NAME', 'HendAxis PoS')
        )
    except Exception:
        pass
