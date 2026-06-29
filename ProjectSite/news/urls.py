# news/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # Новости
    path('', views.news_list_view, name='news_list'),
    path('create/', views.news_create_view, name='news_create'),
    path('<int:news_id>/edit/', views.news_edit_view, name='news_edit'),
    path('<int:news_id>/delete/', views.news_delete_view, name='news_delete'),
    
    # Сотрудничество (админка)
    path('partnership/', views.partnership_list_view, name='partnership_list'),
    path('partnership/<int:partnership_id>/', views.partnership_detail_view, name='partnership_detail'),
    path('partnership/<int:partnership_id>/update-status/', views.partnership_update_status, name='partnership_update_status'),
    path('partnership/<int:partnership_id>/delete/', views.partnership_delete_view, name='partnership_delete'),
]