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

    # API (заглушки — теперь споры в боте)
    path('api/tickets/create/', views.create_ticket),
    path('api/tickets/<int:ticket_id>/reply/', views.add_ticket_reply),
    path('api/tickets/<int:ticket_id>/assign/', views.assign_ticket),
    path('api/tickets/<int:ticket_id>/status/', views.change_ticket_status),
]
