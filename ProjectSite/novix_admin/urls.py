from django.contrib import admin
from django.urls import path, include
from django.shortcuts import render, redirect
from django.conf import settings
from django.conf.urls.static import static
from users import views
from tickets import views as tickets_views
from disputes import views as disputes_views
from news import views as news_views


handler404 = 'novix_admin.views.custom_404'
handler500 = 'novix_admin.views.custom_500'

urlpatterns = [
    # Админ-панель — логин
    path('login/', views.login_view, name='login'),

    # Root → пользовательский сайт
    path('', lambda request: redirect('/usersite/')),
    path('logout/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('users/', views.users_view, name='users'),
    path('users/<int:telegram_id>/', views.user_detail_view, name='user_detail'),
    path('promocodes/', views.promocodes_view, name='promocodes'),
    path('broadcast/', views.broadcast_view, name='broadcast'),
    path('disputes/', disputes_views.disputes_view, name='disputes'),
    path('disputes/<int:dispute_id>/', disputes_views.dispute_detail_view, name='dispute_detail'),
    path('deals/', views.deals_list_view, name='deals_list'),
    path('withdrawals/', views.withdrawals_view, name='withdrawals'),
    path('api/withdrawals/<int:req_id>/approve/', views.withdrawal_approve_api),
    path('api/withdrawals/<int:req_id>/reject/', views.withdrawal_reject_api),
    path('profile/', views.profile_view, name='profile'),
    path('api/profile/update/', views.api_profile_update, name='api_profile_update'),
    path('api/profile/change-password/', views.api_profile_change_password, name='api_profile_change_password'),

    # API: вход
    path('api/login/', views.api_login, name='api_login'),

    # API: пользователи
    path('api/users/<int:telegram_id>/balance/', views.api_change_balance),
    path('api/users/<int:telegram_id>/send-message/', views.api_send_message),
    path('api/users/<int:telegram_id>/messages/', views.api_get_user_messages),
    path('api/users/<int:telegram_id>/grant-premium/', views.api_grant_premium),
    path('api/users/<int:telegram_id>/backup-balance/', views.api_backup_balance),
    path('api/users/<int:telegram_id>/restore-balance/', views.api_restore_balance),
    path('api/users/search/', views.api_search_users, name='api_search_users'),
    path('api/users/<int:telegram_id>/delete-avatar/', views.api_delete_user_avatar),
    path('api/users/export/', views.api_export_users),
    
    # API: Сделки
    path('api/deals/search/', views.api_search_deals, name='api_search_deals'),

    # API: промокоды (code — строка)
    path('api/promocodes/create/', views.api_create_promocode),
    path('api/promocodes/<str:promo_code>/update/', views.api_update_promocode),
    path('api/promocodes/<str:promo_code>/delete/', views.api_delete_promocode),
    path('api/promocodes/<str:promo_code>/', views.api_get_promocode),
    path('api/promocodes/list/', views.api_get_promocodes_list),

        # API: рассылка
    path('api/broadcast/send/', views.api_broadcast_send),

    # Аудит
    path('audit/', views.audit_view, name='audit'),
    path('api/audit/', views.api_get_audit_logs),
    path('api/audit/logs/', views.api_get_audit_logs),
    path('api/audit/clear/', views.api_clear_audit),
    path('api/audit/export/', views.api_export_audit),

    # Администраторы (CEO only)
    path('admins/', views.admins_view, name='admins'),
    path('api/admins/create/', views.api_create_admin),
    path('api/admins/<str:username>/update/', views.api_profile_update),
    path('api/admins/<str:username>/reset-password/', views.api_reset_admin_password),
    path('api/admins/<str:username>/delete/', views.api_delete_admin),

    # API: споры / арбитраж
    path('api/disputes/<int:dispute_id>/', disputes_views.dispute_detail_api),
    path('api/disputes/<int:dispute_id>/resolve/', disputes_views.dispute_resolve_api),

    # API: модерация отзывов
    path('api/reviews/<int:review_id>/moderate/', views.api_moderate_review, name='api_moderate_review'),
    path('api/reviews/reported/', views.api_reported_reviews, name='api_reported_reviews'),

    # Новости
    path('news/', news_views.news_list_view, name='news_list'),
    path('news/create/', news_views.news_create_view, name='news_create'),
    path('news/<int:news_id>/edit/', news_views.news_edit_view, name='news_edit'),
    path('news/<int:news_id>/delete/', news_views.news_delete_view, name='news_delete'),

    # Тикеты поддержки (админка)
    path('tickets/', tickets_views.admin_tickets_view, name='admin_tickets'),
    path('tickets/<int:ticket_id>/', tickets_views.admin_ticket_detail_view, name='admin_ticket_detail'),
    path('api/tickets/<int:ticket_id>/reply/', tickets_views.admin_ticket_reply_api, name='admin_ticket_reply'),
    path('api/tickets/<int:ticket_id>/status/', tickets_views.admin_ticket_status_api, name='admin_ticket_status'),
    path('api/tickets/<int:ticket_id>/assign/', tickets_views.admin_ticket_assign_api, name='admin_ticket_assign'),
    path('api/tickets/<int:ticket_id>/close/', tickets_views.admin_ticket_close_api, name='admin_ticket_close'),

    # Сотрудничество (админка)
    path('news/partnership/', news_views.partnership_list_view, name='partnership_list'),
    path('news/partnership/<int:partnership_id>/', news_views.partnership_detail_view, name='partnership_detail'),
    path('news/partnership/<int:partnership_id>/update-status/', news_views.partnership_update_status, name='partnership_update_status'),
    path('news/partnership/<int:partnership_id>/delete/', news_views.partnership_delete_view, name='partnership_delete'),

    # Ledger
    path('ledger/', views.ledger_view, name='ledger'),

    # Пользовательский сайт
    path('usersite/', include('usersite.urls')),

    # Django admin
    path('django-admin/', admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
