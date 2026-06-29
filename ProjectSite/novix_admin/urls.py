from django.contrib import admin
from django.urls import path, include
from users import views
from news import views as news_views

urlpatterns = [
    # Админ-панель
    path('', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('users/', views.users_view, name='users'),
    path('users/<int:telegram_id>/', views.user_detail_view, name='user_detail'),
    path('promocodes/', views.promocodes_view, name='promocodes'),
    path('broadcast/', views.broadcast_view, name='broadcast'),
    path('disputes/', views.disputes_view, name='disputes'),
    path('profile/', views.profile_view, name='profile'),

    # API: вход
    path('api/login/', views.api_login, name='api_login'),

    # API: пользователи
    path('api/users/<int:telegram_id>/balance/', views.api_change_balance),
    path('api/users/<int:telegram_id>/send-message/', views.api_send_message),
    path('api/users/<int:telegram_id>/grant-premium/', views.api_grant_premium),
    path('api/users/<int:telegram_id>/backup-balance/', views.api_backup_balance),
    path('api/users/<int:telegram_id>/restore-balance/', views.api_restore_balance),
    path('api/users/export/', views.api_export_users),

    # API: промокоды (code — строка)
    path('api/promocodes/create/', views.api_create_promocode),
    path('api/promocodes/<str:promo_code>/update/', views.api_update_promocode),
    path('api/promocodes/<str:promo_code>/delete/', views.api_delete_promocode),
    path('api/promocodes/<str:promo_code>/', views.api_get_promocode),
    path('api/promocodes/list/', views.api_get_promocodes_list),

    # API: аудит
    path('api/audit/logs/', views.api_get_audit_logs),
    path('api/audit/clear/', views.api_clear_audit),
    path('api/audit/export/', views.api_export_audit),

    # API: споры / арбитраж
    path('api/disputes/<int:dispute_id>/', views.dispute_detail_api),
    path('api/disputes/<int:dispute_id>/resolve/', views.dispute_resolve_api),

    # Новости
    path('news/', news_views.news_list_view, name='news_list'),
    path('news/create/', news_views.news_create_view, name='news_create'),
    path('news/<int:news_id>/edit/', news_views.news_edit_view, name='news_edit'),
    path('news/<int:news_id>/delete/', news_views.news_delete_view, name='news_delete'),

    # Тикеты поддержки (админка)
    path('tickets/', views.admin_tickets_view, name='admin_tickets'),
    path('tickets/<int:ticket_id>/', views.admin_ticket_detail_view, name='admin_ticket_detail'),
    path('api/tickets/<int:ticket_id>/reply/', views.admin_ticket_reply_api, name='admin_ticket_reply'),
    path('api/tickets/<int:ticket_id>/status/', views.admin_ticket_status_api, name='admin_ticket_status'),
    path('api/tickets/<int:ticket_id>/assign/', views.admin_ticket_assign_api, name='admin_ticket_assign'),
    path('api/tickets/<int:ticket_id>/close/', views.admin_ticket_close_api, name='admin_ticket_close'),

    # Сотрудничество (админка)
    path('news/partnership/', news_views.partnership_list_view, name='partnership_list'),
    path('news/partnership/<int:partnership_id>/', news_views.partnership_detail_view, name='partnership_detail'),
    path('news/partnership/<int:partnership_id>/update-status/', news_views.partnership_update_status, name='partnership_update_status'),
    path('news/partnership/<int:partnership_id>/delete/', news_views.partnership_delete_view, name='partnership_delete'),

    # Пользовательский сайт
    path('usersite/', include('usersite.urls')),

    # Django admin
    path('django-admin/', admin.site.urls),
]
