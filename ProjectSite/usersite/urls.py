from django.urls import path
from django.shortcuts import redirect
from . import views
from news import views as news_views

urlpatterns = [
    path('', views.landing_view, name='landing'),
    path('login/', views.user_login_view, name='user_login'),
    path('telegram-auth/', views.telegram_auth_view, name='telegram_auth'),
    path('dashboard/', lambda req: redirect('/usersite/profile/')),
    path('profile/', views.profile_view, name='user_profile'),
    path('profile/<int:user_id>/', views.user_profile_redirect, name='user_profile_redirect'),
    path('profile/<str:username>/', views.public_profile_view, name='public_profile'),
    path('settings/', views.settings_view, name='user_settings'),
    path('api/update-profile/', views.api_update_profile, name='api_update_profile'),
    path('top/', views.forbes_view, name='top'),
    path('avatar/<int:user_id>/', views.avatar_serve_view, name='avatar_serve'),
    path('api/upload-avatar/', views.api_upload_avatar, name='api_upload_avatar'),
    path('terms/', views.terms_view, name='terms'),
    path('privacy/', views.privacy_view, name='privacy'),
    path('logout/', views.logout_view, name='user_logout'),
    path('tickets/', views.user_tickets_view, name='user_tickets'),
    path('tickets/new/', views.user_ticket_new_view, name='user_ticket_new'),
    path('tickets/<int:ticket_id>/', views.user_ticket_detail_view, name='user_ticket_detail'),

    # Сотрудничество (пользовательская часть)
    path('partnership/', news_views.partnership_form_view, name='partnership_form'),
    path('partnership/my/', news_views.partnership_my_view, name='partnership_my'),
    path('partnership/<int:partnership_id>/', news_views.partnership_user_detail_view, name='partnership_user_detail'),

    # Регистрация профиля (после Telegram-входа)
    path('register/', views.register_profile_view, name='register_profile'),
    path('api/save-profile/', views.save_profile_api, name='save_profile'),
    path('api/local-login/', views.local_login_api, name='local_login'),

    # Premium wizard
    path('premium/', views.premium_wizard_view, name='premium_wizard'),

    # API тикетов
    path('transactions/', views.transactions_view, name='transactions'),
    path('withdraw/', views.withdraw_view, name='withdraw'),
    path('api/withdraw/create/', views.withdraw_create_api),
    path('api/notifications/read/', views.notifications_mark_read, name='notifications_mark_read'),
    path('api/request-code/', views.request_code_api, name='request_code'),
    path('api/tickets/create/', views.create_ticket),
    path('api/tickets/<int:ticket_id>/reply/', views.add_ticket_reply),
    path('api/tickets/<int:ticket_id>/close/', views.close_ticket),
    path('api/tickets/<int:ticket_id>/assign/', views.assign_ticket),
    path('api/tickets/<int:ticket_id>/status/', views.change_ticket_status),

    # Создание сделки
    path('deal/create/', views.create_deal_view, name='create_deal'),
    path('deal/success/', views.deal_success_view, name='deal_success'),

    # Детали сделки + оплата (Usersite)
    path('deal/<int:deal_id>/', views.deal_detail_view, name='deal_detail'),
    path('deal/<int:deal_id>/pay/', views.deal_pay_view, name='deal_pay'),

    # API действий со сделкой (server-to-server к bot.py)
    path('api/deal/<int:deal_id>/pay/', views.api_deal_pay, name='api_deal_pay'),
    path('api/deal/<int:deal_id>/mark-sent/', views.api_deal_mark_sent, name='api_deal_mark_sent'),
    path('api/deal/<int:deal_id>/confirm-receipt/', views.api_deal_confirm_receipt, name='api_deal_confirm_receipt'),

    # Отзывы
    path('reviews/', views.reviews_view, name='user_reviews'),
    path('api/reviews/update/', views.update_review_api, name='update_review'),
    path('api/reviews/report/', views.report_review_api, name='report_review'),
    path('api/profile-reviews/create/', views.api_create_profile_review, name='api_create_profile_review'),
    path('api/profile-reviews/update/', views.api_update_profile_review, name='api_update_profile_review'),
    path('api/profile-reviews/delete/', views.api_delete_profile_review, name='api_delete_profile_review'),

    # Восстановление пароля (email recovery)
    path('password-reset/', views.password_reset_request_view, name='password_reset_request'),
    path('api/password-reset-request/', views.api_password_reset_request, name='api_password_reset_request'),
    path('password-reset/<str:token>/', views.password_reset_confirm_view, name='password_reset_confirm'),
    path('api/password-reset-confirm/', views.api_password_reset_confirm, name='api_password_reset_confirm'),

    # Уведомления
    path('notifications/', views.notifications_view, name='user_notifications'),
    path('notifications/<int:notif_id>/open/', views.notification_open_view, name='notification_open'),
    path('api/notifications/unread-count/', views.api_notifications_unread_count, name='api_notifications_unread'),
    path('api/notifications/prefs/', views.api_notification_prefs, name='api_notification_prefs'),

    # Журнал безопасности
    path('settings/security/', views.security_log_view, name='security_log'),

    # P2P перевод между пользователями
    path('send/', views.send_view, name='send'),
    path('api/send/preview/', views.api_send_preview, name='api_send_preview'),
    path('api/send/confirm/', views.api_send_confirm, name='api_send_confirm'),
    # Сохранение платёжных реквизитов с 2FA
    path('api/save-payment-details/', views.api_save_payment_details, name='api_save_payment_details'),

    # P2P обмен валют
    path('exchange/', views.exchange_view, name='exchange'),
    path('api/exchange/create/', views.api_exchange_create_offer, name='api_exchange_create'),
    path('api/exchange/accept/', views.api_exchange_accept, name='api_exchange_accept'),
    path('api/exchange/cancel/', views.api_exchange_cancel, name='api_exchange_cancel'),
]
