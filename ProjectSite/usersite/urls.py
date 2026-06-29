from django.urls import path
from django.shortcuts import redirect
from . import views

urlpatterns = [
    path('', lambda request: redirect('/usersite/login/')),
    path('login/', views.user_login_view, name='user_login'),
    path('telegram-auth/', views.telegram_auth_view, name='telegram_auth'),
    path('test-login/', views.test_login, name='test_login'),
    path('dashboard/', views.dashboard_view, name='user_dashboard'),
    path('profile/', views.profile_view, name='user_profile'),
    path('profile/<int:user_id>/', views.user_profile_redirect, name='user_profile_redirect'),
    path('logout/', views.logout_view, name='user_logout'),
    path('tickets/', views.user_tickets_view, name='user_tickets'),
    path('tickets/new/', views.user_ticket_new_view, name='user_ticket_new'),
    path('tickets/<int:ticket_id>/', views.user_ticket_detail_view, name='user_ticket_detail'),

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
]
