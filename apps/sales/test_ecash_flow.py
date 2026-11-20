import json
import uuid
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client
from django.urls import reverse

from apps.core.models import Tenant, Role, User, Location
from apps.customers.models import Customer
from apps.inventory.models import Product, Category
from apps.payments.models import PaymentProviderConfig, ShopPaymentAssignment
from apps.sales.models import Sale, Shift
from apps.payments.services.base import PaymentResult


class ECashCheckoutFlowTests(TestCase):
    def setUp(self):
        self.client = Client()

        self.tenant = Tenant.objects.create(
            name="ECash Test Tenant",
            slug="ecash-test-tenant",
            email="ecash@tenant.com"
        )

        self.shop_manager_role, _ = Role.objects.get_or_create(name='SHOP_MANAGER')

        self.shop = Location.objects.create(
            tenant=self.tenant,
            name="Osu Branch",
            location_type="SHOP"
        )

        self.user = User.objects.create_user(
            email="cashier@osu.com",
            password="password123",
            tenant=self.tenant,
            role=self.shop_manager_role,
            location=self.shop,
            first_name="Ama",
            last_name="Serwaa"
        )

        self.client.login(email="cashier@osu.com", password="password123")

        # Open shift
        self.shift = Shift.objects.create(
            tenant=self.tenant,
            shop=self.shop,
            attendant=self.user,
            status='OPEN'
        )

        self.category = Category.objects.create(tenant=self.tenant, name="Electronics")
        self.product = Product.objects.create(
            tenant=self.tenant,
            name="Power Bank 20000mAh",
            sku="PB-20K",
            category=self.category,
            default_selling_price=Decimal("150.00")
        )

        self.customer = Customer.objects.create(
            tenant=self.tenant,
            name="Kofi Mensah",
            phone="0241234567",
            shop=self.shop
        )

        # Provider configs
        self.paystack_config = PaymentProviderConfig.objects.create(
            tenant=self.tenant,
            provider="PAYSTACK",
            nickname="Main Paystack",
            public_key="pk_test_sample",
            secret_key="sk_test_sample",
            is_active=True
        )
        ShopPaymentAssignment.objects.create(
            tenant=self.tenant,
            shop=self.shop,
            provider_config=self.paystack_config,
            priority=1
        )

        self.expresspay_config = PaymentProviderConfig.objects.create(
            tenant=self.tenant,
            provider="EXPRESSPAY",
            nickname="Main ExpressPay",
            merchant_id="merchant_123",
            secret_key="secret_123",
            is_active=True
        )

    def test_initialize_paystack_with_phone_normalization_and_carrier(self):
        """Test Paystack initialization extracts phone, normalizes it and identifies MTN."""
        url = reverse('sales:initialize_ecash_payment')
        payload = {
            'items': [{'product_id': self.product.id, 'quantity': 1, 'unit_price': '150.00'}],
            'discount_amount': 0,
            'customer_id': self.customer.id,
            'total': '150.00',
            'provider_config_id': self.paystack_config.id,
            'phone': '0241234567'
        }

        response = self.client.post(url, json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertTrue(data['success'])
        self.assertEqual(data['checkout_type'], 'inline')
        self.assertEqual(data['phone'], '0241234567')
        self.assertEqual(data['phone_intl'], '+233241234567')
        self.assertEqual(data['network'], 'MTN')
        self.assertEqual(data['paystack_provider'], 'mtn')
        self.assertEqual(data['customer_email'], 'momo_233241234567@hendaxis.com')
        self.assertIsNotNone(data['sale_id'])

        # Verify sale in database
        sale = Sale.objects.get(pk=data['sale_id'])
        self.assertEqual(sale.paystack_reference, data['reference'])
        self.assertEqual(sale.status, 'PENDING')

    def test_phone_normalization_intl_prefix_and_vodafone(self):
        """Test international +233 prefix converts to local 020 and detects Vodafone."""
        url = reverse('sales:initialize_ecash_payment')
        payload = {
            'items': [{'product_id': self.product.id, 'quantity': 1, 'unit_price': '150.00'}],
            'discount_amount': 0,
            'customer_id': None,
            'total': '150.00',
            'provider_config_id': self.paystack_config.id,
            'phone': '+233209876543'
        }

        response = self.client.post(url, json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertTrue(data['success'])
        self.assertEqual(data['phone'], '0209876543')
        self.assertEqual(data['phone_intl'], '+233209876543')
        self.assertEqual(data['network'], 'VODAFONE')
        self.assertEqual(data['paystack_provider'], 'vod')

    def test_initialize_expresspay_redirect_prefills_metadata_and_token(self):
        """Test ExpressPay initialization forwards phonenumber and metadata properly."""
        url = reverse('sales:initialize_ecash_payment')
        payload = {
            'items': [{'product_id': self.product.id, 'quantity': 1, 'unit_price': '150.00'}],
            'discount_amount': 0,
            'customer_id': self.customer.id,
            'total': '150.00',
            'provider_config_id': self.expresspay_config.id,
            'phone': '0555555555'
        }

        mock_result = PaymentResult(
            success=True,
            reference="ECASH-TEST-REF",
            authorization_url="https://sandbox.expresspaygh.com/api/checkout.php?token=tok_abc123",
            data={'token': 'tok_abc123', 'status': 1}
        )

        with patch('apps.payments.services.expresspay.ExpressPayProvider.initialize_payment', return_value=mock_result) as mock_init:
            response = self.client.post(url, json.dumps(payload), content_type='application/json')
            self.assertEqual(response.status_code, 200)
            data = response.json()

            self.assertTrue(data['success'])
            self.assertEqual(data['checkout_type'], 'redirect')
            self.assertEqual(data['authorization_url'], mock_result.authorization_url)
            self.assertEqual(data['token'], 'tok_abc123')
            self.assertEqual(data['phone'], '0555555555')

            # Verify mock was called with metadata containing customer number
            call_kwargs = mock_init.call_args[1]
            metadata = call_kwargs.get('metadata', {})
            self.assertEqual(metadata.get('phone'), '0555555555')
            self.assertEqual(metadata.get('phonenumber'), '0555555555')
            self.assertEqual(metadata.get('network'), 'MTN')

    def test_verify_ecash_payment_idempotency_for_completed_sale(self):
        """Test that re-verifying an already completed sale returns success without re-processing."""
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop,
            attendant=self.user,
            shift=self.shift,
            customer=self.customer,
            payment_method='ECASH',
            status='COMPLETED',
            paystack_reference='ECASH-ALREADY-DONE',
            total=Decimal('150.00')
        )

        url = reverse('sales:verify_ecash_payment')
        payload = {
            'reference': 'ECASH-ALREADY-DONE',
            'sale_id': sale.pk,
            'amount': 150.00,
            'provider_config_id': self.paystack_config.id
        }

        response = self.client.post(url, json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertTrue(data['success'])
        self.assertTrue(data.get('already_completed'))
        self.assertEqual(data['sale_id'], sale.pk)
