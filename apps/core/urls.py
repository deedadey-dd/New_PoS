"""
URL configuration for the core app.
"""
from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    # Home / Landing page
    path('', views.HomePageView.as_view(), name='home'),
    # Demo
    path('demo/', views.DemoHubView.as_view(), name='demo_hub'),
    path('demo/login/<str:role>/', views.DemoAutoLoginView.as_view(), name='demo_login'),

    # Authentication
    path('login/', views.LoginView.as_view(), name='login'),
    path('logout/', views.LogoutView.as_view(), name='logout'),

    
    # Tenant setup
    path('setup/', views.TenantSetupView.as_view(), name='tenant_setup'),
    
    # Dashboard
    path('dashboard/', views.DashboardView.as_view(), name='dashboard'),
    path('auditor/', views.AuditorDashboardView.as_view(), name='auditor_dashboard'),
    path('dispatch-summary/', views.DispatchSummaryView.as_view(), name='dispatch_summary'),
    path('settings/', views.SettingsView.as_view(), name='settings'),
    
    # Locations
    path('locations/', views.LocationListView.as_view(), name='location_list'),
    path('locations/create/', views.LocationCreateView.as_view(), name='location_create'),
    path('locations/<int:pk>/edit/', views.LocationUpdateView.as_view(), name='location_edit'),
    path('locations/<int:pk>/delete/', views.LocationDeleteView.as_view(), name='location_delete'),
    path('locations/<int:pk>/overview/', views.LocationOverviewView.as_view(), name='location_overview'),
    
    # Users
    path('users/', views.UserListView.as_view(), name='user_list'),
    path('users/create/', views.UserCreateView.as_view(), name='user_create'),
    path('users/<int:pk>/edit/', views.UserUpdateView.as_view(), name='user_edit'),
    path('users/<int:pk>/delete/', views.UserDeleteView.as_view(), name='user_delete'),
    path('users/<int:pk>/reset-password/', views.AdminPasswordResetView.as_view(), name='admin_password_reset'),
    path('users/<int:pk>/overview/', views.UserOverviewView.as_view(), name='user_overview'),
    
    # Password change
    path('change-password/', views.ForcedPasswordChangeView.as_view(), name='forced_password_change'),
    
    # Help
    path('help/', views.HelpView.as_view(), name='help'),
    path('documentation/', views.DocumentationView.as_view(), name='documentation'),
    
    # Startup Kit / Get Started
    path('get-started/', views.StartupKitView.as_view(), name='startup_kit'),
    
    # Contact form
    path('contact/submit/', views.ContactSubmitView.as_view(), name='contact_submit'),
]
