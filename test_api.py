from django.test import RequestFactory
from apps.sales.views import api_sale_detail
from apps.sales.models import Sale
from apps.core.models import User

user = User.objects.filter(role__name='SHOP_MANAGER').first()
sale = Sale.objects.filter(status='PENDING_DISPATCH', tenant=user.tenant).first()

if sale:
    print(f"Testing api_sale_detail for sale {sale.id}")
    factory = RequestFactory()
    request = factory.get(f'/sales/api/{sale.id}/detail/')
    request.user = user
    try:
        response = api_sale_detail(request, sale.id)
        print("Status code:", response.status_code)
        print("Content:", response.content)
    except Exception as e:
        import traceback
        traceback.print_exc()
else:
    print("No PENDING_DISPATCH sale found")
