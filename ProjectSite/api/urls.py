# api/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.login_api, name='api_login'),
    path('jwt-login/', views.jwt_login_api, name='api_jwt_login'),
    path('dashboard/', views.dashboard_api, name='api_dashboard'),
    path('users/', views.users_api, name='api_users'),
    path('broadcast/', views.broadcast_api, name='api_broadcast'),
    path('promocodes/', views.promocodes_api, name='api_promocodes'),
    path('promocodes/create/', views.create_promocode_api, name='api_create_promocode'),
    path('promocodes/<str:promo_code>/', views.get_promocode_api, name='api_get_promocode'),
    path('promocodes/<str:promo_code>/update/', views.update_promocode_api, name='api_update_promocode'),
    path('promocodes/<str:promo_code>/delete/', views.delete_promocode_api, name='api_delete_promocode'),
    path('audit/', views.audit_api, name='api_audit'),
    path('profile/', views.profile_api, name='api_profile'),
]