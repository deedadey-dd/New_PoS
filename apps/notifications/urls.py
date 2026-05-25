"""
URL configuration for notifications app.
"""
from django.urls import path
from . import views

app_name = 'notifications'

urlpatterns = [
    path('', views.NotificationListView.as_view(), name='notification_list'),
    path('mark-all-read/', views.mark_all_read, name='mark_all_read'),
    path('<int:pk>/read/', views.mark_as_read, name='mark_as_read'),
    path('api/<int:pk>/read/', views.mark_notification_read_api, name='api_mark_read'),
    
    # Bulletin Board
    path('bulletin/', views.BulletinBoardView.as_view(), name='bulletin_board'),
    path('bulletin/new/', views.BulletinPostCreateView.as_view(), name='bulletin_post_create'),
    path('bulletin/<int:pk>/delete/', views.BulletinPostDeleteView.as_view(), name='bulletin_post_delete'),
    path('bulletin/<int:pk>/read/', views.bulletin_mark_read, name='bulletin_mark_read'),
    path('bulletin/mark-all-read/', views.bulletin_mark_all_read, name='bulletin_mark_all_read'),
]
