"""
Sitemap configuration for Hendaxis POS.
Covers all public-facing pages to improve Google indexing.

NOTE: The domain used in sitemap URLs is controlled by the django.contrib.sites
framework (database). Run this to update it:
  python manage.py shell -c "from django.contrib.sites.models import Site;
  s=Site.objects.get(id=1); s.domain='pos.hendaxis.com'; s.name='Hendaxis POS'; s.save()"
"""
from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class StaticViewSitemap(Sitemap):
    """Sitemap for static public-facing pages."""
    protocol = 'https'
    changefreq = 'weekly'
    priority = 0.8

    def items(self):
        return [
            'home',
            'help',
            'documentation',
            'demo_hub',
            'startup_kit',
        ]

    def location(self, item):
        return reverse(f'core:{item}')


class PricingPageSitemap(Sitemap):
    """Sitemap for the public pricing page."""
    protocol = 'https'
    changefreq = 'monthly'
    priority = 0.9

    def items(self):
        return ['pricing']

    def location(self, item):
        return reverse(f'subscriptions:{item}')
