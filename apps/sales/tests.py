from django.test import TestCase
from apps.sales.forms import ShopManagerSettingsForm
from apps.sales.models import ShopSettings
from apps.core.models import Tenant, Location, User

class ShopManagerSettingsFormTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Tenant", is_active=True)
        self.location = Location.objects.create(
            tenant=self.tenant,
            name="Test Shop",
            location_type="SHOP"
        )
        self.shop_settings = ShopSettings.objects.create(
            tenant=self.tenant,
            shop=self.location,
            auto_print_receipts=True
        )

    def test_form_includes_auto_print_receipts(self):
        form = ShopManagerSettingsForm()
        self.assertIn('auto_print_receipts', form.fields)

    def test_form_saves_auto_print_receipts(self):
        data = {
            'hide_zero_stock_in_pos': False,
            'auto_print_receipts': False,
            'receipt_printer_type': 'THERMAL_80MM',
            'show_logo_on_receipt': True,
            'receipt_header': 'Test Header',
            'receipt_footer': 'Test Footer',
            'enable_momo_payment': True,
        }
        form = ShopManagerSettingsForm(data=data, instance=self.shop_settings)
        self.assertTrue(form.is_valid())
        saved_settings = form.save()
        self.assertFalse(saved_settings.auto_print_receipts)
