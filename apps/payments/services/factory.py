"""
Payment provider factory.
"""
from typing import Optional

from .base import BasePaymentProvider
from .paystack import PaystackProvider
from .flutterwave import FlutterwaveProvider
from .appsnmobile import AppsNMobileProvider
from .hubtel import HubtelProvider
from .nalo import NaloProvider


def get_payment_provider(tenant, shop=None) -> Optional[BasePaymentProvider]:
    """
    Factory function to get the active payment provider for a tenant/shop.
    
    Args:
        tenant: Tenant model instance
        shop: Optional Location model instance for shop-specific overrides
        
    Returns:
        Payment provider instance or None
    """
    from apps.payments.models import PaymentProviderSettings
    
    settings = None
    
    # Check for shop-specific override first
    if shop:
        settings = PaymentProviderSettings.objects.filter(
            tenant=tenant,
            shop=shop,
            is_active=True
        ).order_by('priority', 'provider').first()
        
    # Fallback to tenant-wide configuration
    if not settings:
        settings = PaymentProviderSettings.objects.filter(
            tenant=tenant,
            shop__isnull=True,
            is_active=True
        ).order_by('priority', 'provider').first()
        
    if not settings:
        return None
        
    provider_name = getattr(settings, 'provider', None)
    
    if provider_name == 'PAYSTACK':
        return PaystackProvider(settings)
    elif provider_name == 'FLUTTERWAVE':
        return FlutterwaveProvider(settings)
    elif provider_name == 'APPSNMOBILE':
        return AppsNMobileProvider(settings)
    elif provider_name == 'HUBTEL':
        return HubtelProvider(settings)
    elif provider_name == 'NALO':
        return NaloProvider(settings)
    
    return None


def get_active_payment_providers(tenant, shop=None) -> list[BasePaymentProvider]:
    """
    Factory function to get all active payment providers for a tenant/shop, ordered by priority.
    
    Args:
        tenant: Tenant model instance
        shop: Optional Location model instance for shop-specific overrides
        
    Returns:
        List of payment provider instances
    """
    from apps.payments.models import PaymentProviderSettings
    
    # Try shop-specific settings first
    settings_qs = PaymentProviderSettings.objects.none()
    if shop:
        settings_qs = PaymentProviderSettings.objects.filter(
            tenant=tenant,
            shop=shop,
            is_active=True
        ).order_by('priority', 'provider')
        
    # Fallback to tenant-wide configuration if no shop settings found
    if not settings_qs.exists():
        settings_qs = PaymentProviderSettings.objects.filter(
            tenant=tenant,
            shop__isnull=True,
            is_active=True
        ).order_by('priority', 'provider')
        
    providers = []
    for setting in settings_qs:
        provider_name = getattr(setting, 'provider', None)
        if provider_name == 'PAYSTACK':
            providers.append(PaystackProvider(setting))
        elif provider_name == 'FLUTTERWAVE':
            providers.append(FlutterwaveProvider(setting))
        elif provider_name == 'APPSNMOBILE':
            providers.append(AppsNMobileProvider(setting))
        elif provider_name == 'HUBTEL':
            providers.append(HubtelProvider(setting))
        elif provider_name == 'NALO':
            providers.append(NaloProvider(setting))
            
    return providers

