# news/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib.admin.views.decorators import staff_member_required
from django.utils import timezone
import json
from .models import News, Partnership, PartnershipMessage

# ============= НОВОСТИ (АДМИНКА) =============

def news_list_view(request):
    """Список новостей"""
    news_list = News.objects.all().order_by('-created_at')
    return render(request, 'news_list.html', {
        'active_page': 'news',
        'news_list': news_list,
    })

def news_create_view(request):
    """Создание новости"""
    if request.method == 'POST':
        title = request.POST.get('title')
        short_description = request.POST.get('short_description')
        description = request.POST.get('description', '')
        content = request.POST.get('content')
        is_published = request.POST.get('is_published') == 'on'
        
        News.objects.create(
            title=title,
            short_description=short_description,
            description=description,
            content=content,
            is_published=is_published
        )
        return redirect('/news/')
    
    return render(request, 'news_form.html', {
        'active_page': 'news',
        'news': None,
        'title': 'Создать новость',
    })

def news_edit_view(request, news_id):
    """Редактирование новости"""
    news = get_object_or_404(News, id=news_id)
    
    if request.method == 'POST':
        news.title = request.POST.get('title')
        news.short_description = request.POST.get('short_description')
        news.description = request.POST.get('description', '')
        news.content = request.POST.get('content')
        news.is_published = request.POST.get('is_published') == 'on'
        news.save()
        return redirect('/news/')
    
    return render(request, 'news_form.html', {
        'active_page': 'news',
        'news': news,
        'title': 'Редактировать новость',
    })

@csrf_exempt
@require_http_methods(["POST"])
def news_delete_view(request, news_id):
    """Удаление новости"""
    news = get_object_or_404(News, id=news_id)
    news.delete()
    return JsonResponse({'success': True})


# ============= СОТРУДНИЧЕСТВО (АДМИНКА) =============

@staff_member_required(login_url='/')
def partnership_list_view(request):
    """Список заявок на сотрудничество (админка)"""
    partnerships = Partnership.objects.all().order_by('-created_at')
    return render(request, 'partnership_list.html', {
        'active_page': 'partnership',
        'partnerships': partnerships,
    })

@staff_member_required(login_url='/')
def partnership_detail_view(request, partnership_id):
    """Детальный просмотр заявки с чатом (админка)"""
    partnership = get_object_or_404(Partnership, id=partnership_id)
    messages = PartnershipMessage.objects.filter(partnership=partnership).order_by('created_at')
    
    if request.method == 'POST':
        data = json.loads(request.body)
        message_text = data.get('message')
        
        if message_text:
            PartnershipMessage.objects.create(
                partnership=partnership,
                sender_type='admin',
                sender_name=request.session.get('admin', 'Admin'),
                message=message_text
            )
            partnership.updated_at = timezone.now()
            partnership.save()
            return JsonResponse({'success': True})
    
    return render(request, 'partnership_detail.html', {
        'active_page': 'partnership',
        'partnership': partnership,
        'messages': messages,
    })

@staff_member_required(login_url='/')
@csrf_exempt
def partnership_update_status(request, partnership_id):
    """Обновление статуса заявки"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)
    
    data = json.loads(request.body)
    new_status = data.get('status')
    
    valid_statuses = ['pending', 'approved', 'rejected']
    if new_status not in valid_statuses:
        return JsonResponse({'success': False, 'error': 'Invalid status'}, status=400)
    
    partnership = get_object_or_404(Partnership, id=partnership_id)
    partnership.status = new_status
    partnership.updated_at = timezone.now()
    partnership.save()
    
    status_text = {
        'pending': 'Ожидает ответа',
        'approved': 'Одобрено',
        'rejected': 'Отказано'
    }.get(new_status, new_status)
    
    PartnershipMessage.objects.create(
        partnership=partnership,
        sender_type='admin',
        sender_name='Система',
        message=f"Статус заявки изменён на: {status_text}"
    )
    
    return JsonResponse({'success': True})

@staff_member_required(login_url='/')
@csrf_exempt
def partnership_delete_view(request, partnership_id):
    """Удаление заявки"""
    partnership = get_object_or_404(Partnership, id=partnership_id)
    partnership.delete()
    return JsonResponse({'success': True})


# ============= СОТРУДНИЧЕСТВО (ПОЛЬЗОВАТЕЛЬСКАЯ ЧАСТЬ) =============

def partnership_form_view(request):
    """Страница создания заявки на сотрудничество (пользователь)"""
    if not request.session.get('user_id'):
        return redirect('/usersite/login/')
    
    if request.method == 'POST':
        user_id = request.session.get('user_id')
        full_name = request.POST.get('full_name')
        email = request.POST.get('email')
        telegram = request.POST.get('telegram')
        partnership_type = request.POST.get('partnership_type')
        description = request.POST.get('description')
        social_links = request.POST.get('social_links', '')
        
        partnership = Partnership.objects.create(
            user_id=user_id,
            full_name=full_name,
            email=email,
            telegram=telegram,
            partnership_type=partnership_type,
            description=description,
            social_links=social_links,
            status='pending'
        )
        
        # Добавляем первое сообщение в чат
        PartnershipMessage.objects.create(
            partnership=partnership,
            sender_type='user',
            sender_name=full_name,
            message=f"📝 Новая заявка на сотрудничество\nТип: {dict(Partnership.TYPE_CHOICES).get(partnership_type, partnership_type)}\n\n{description}"
        )
        
        return redirect('/usersite/partnership/my/')
    
    return render(request, 'usersite/partnership_form.html', {
        'title': 'Заявка на сотрудничество',
    })

def partnership_my_view(request):
    """Список моих заявок на сотрудничество (пользователь)"""
    if not request.session.get('user_id'):
        return redirect('/usersite/login/')
    
    user_id = request.session.get('user_id')
    partnerships = Partnership.objects.filter(user_id=user_id).order_by('-created_at')
    
    return render(request, 'usersite/partnership_my.html', {
        'partnerships': partnerships,
    })

def partnership_user_detail_view(request, partnership_id):
    """Детальный просмотр заявки (пользователь)"""
    if not request.session.get('user_id'):
        return redirect('/usersite/login/')
    
    user_id = request.session.get('user_id')
    partnership = get_object_or_404(Partnership, id=partnership_id, user_id=user_id)
    messages = PartnershipMessage.objects.filter(partnership=partnership).order_by('created_at')
    
    if request.method == 'POST':
        data = json.loads(request.body)
        message_text = data.get('message')
        
        if message_text and partnership.status != 'rejected':
            PartnershipMessage.objects.create(
                partnership=partnership,
                sender_type='user',
                sender_name=partnership.full_name,
                message=message_text
            )
            partnership.updated_at = timezone.now()
            partnership.save()
            return JsonResponse({'success': True})
    
    return render(request, 'usersite/partnership_detail_user.html', {
        'partnership': partnership,
        'messages': messages,
    })