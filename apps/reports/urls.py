from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    path('end-of-day/', views.EndOfDayDetailsView.as_view(), name='end_of_day_summary'),
    path('end-of-day/management/', views.EndOfDayManagementView.as_view(), name='end_of_day_management'),
]
