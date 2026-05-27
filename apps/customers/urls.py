from django.urls import path
from . import views

app_name = 'customers'

urlpatterns = [
    path('', views.CustomerListView.as_view(), name='customer_list'),
    path('add/', views.CustomerCreateView.as_view(), name='customer_create'),
    path('<int:pk>/', views.CustomerDetailView.as_view(), name='customer_detail'),
    path('<int:pk>/edit/', views.CustomerUpdateView.as_view(), name='customer_edit'),
    path('<int:pk>/payment/', views.CustomerPaymentView.as_view(), name='customer_payment'),
    path('payment/<int:pk>/receipt/', views.PaymentReceiptView.as_view(), name='payment_receipt'),

    # Excel Export
    path('export/', views.CustomerListExportView.as_view(), name='customer_list_export'),
    
    # Credit Ledger
    path('credit-ledger/', views.CustomerCreditLedgerView.as_view(), name='credit_ledger'),
    path('credit-ledger/export/', views.CustomerCreditLedgerExportView.as_view(), name='credit_ledger_export'),
]
