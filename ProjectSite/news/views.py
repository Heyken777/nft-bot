# news/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
import json, os, sqlite3
from .models import News, Partnership, PartnershipMessage
from users.views import has_permission, OWNER_TELEGRAM_ID

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, '..', 'novixgift.db')


def _get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '0.0.0.0')


def _get_admin_name(request):
    return 'Heyken' if request.session.get('telegram_id') == OWNER_TELEGRAM_ID else request.session.get('username', 'Администратор')


def _log_admin_action(request, action: str, target_id=None):
    admin_id = request.session.get('telegram_id', 0)
    admin_name = 'Arkadiex' if admin_id == OWNER_TELEGRAM_ID else request.session.get('username', '')
    ip = _get_client_ip(request)
    now = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        conn = sqlite3.connect(DB_PATH, timeout=40)
        cur = conn.cursor()
        desc = f"CEO / Владелец Heyken совершил действие: {action}" if admin_id == OWNER_TELEGRAM_ID else action
        if target_id:
            desc += f" | id={target_id}"
        desc += f" | IP: {ip}"
        cur.execute(
            "INSERT INTO audit_logs (timestamp, user_id, username, action_type, description, ip_address) VALUES (?, ?, ?, ?, ?, ?)",
            (now, admin_id, admin_name, action, desc, ip)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _log_page_view(request, action_type: str, description: str):
    admin_id = request.session.get('telegram_id', 0)
    admin_name = 'Arkadiex' if admin_id == OWNER_TELEGRAM_ID else request.session.get('username', '')
    ip = _get_client_ip(request)
    now = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        conn = sqlite3.connect(DB_PATH, timeout=40)
        cur = conn.cursor()
        desc = f"👑 {description} | IP: {ip}" if admin_id == OWNER_TELEGRAM_ID else f"{description} | IP: {ip}"
        cur.execute(
            "INSERT INTO audit_logs (timestamp, user_id, username, action_type, description, ip_address) VALUES (?, ?, ?, ?, ?, ?)",
            (now, admin_id, admin_name, action_type, desc, ip)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

# ============= НОВОСТИ (АДМИНКА) =============

def news_list_view(request):
    """Список новостей"""
    if not request.session.get('telegram_id'):
        return redirect('/')
    _log_page_view(request, 'Просмотр Новостей', 'Администратор открыл список новостей')
    news_list = []
    try:
        news_list = News.objects.all().order_by('-created_at')
    except Exception as e:
        print(f"[news_list] Error: {e}")
    return render(request, 'news_list.html', {
        'active_page': 'news',
        'admin_name': _get_admin_name(request),
        'news_list': news_list,
    })

def news_create_view(request):
    """Создание новости"""
    if not request.session.get('telegram_id'):
        return redirect('/')
    _log_page_view(request, 'Просмотр Создания Новости', 'Администратор открыл страницу создания новости')
    try:
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
            _log_admin_action(request, f"Создал новость: {title}")
            return redirect('/news/')
    except Exception as e:
        print(f"[news_create] Error: {e}")
        return redirect('/news/')
    
    return render(request, 'news_form.html', {
        'active_page': 'news',
        'admin_name': _get_admin_name(request),
        'news': None,
        'title': 'Создать новость',
    })

def news_edit_view(request, news_id):
    """Редактирование новости"""
    if not request.session.get('telegram_id'):
        return redirect('/')
    _log_page_view(request, 'Просмотр Редактирования Новости', f'Администратор открыл редактирование новости #{news_id}')
    news = None
    try:
        news = get_object_or_404(News, id=news_id)
        
        if request.method == 'POST':
            news.title = request.POST.get('title')
            news.short_description = request.POST.get('short_description')
            news.description = request.POST.get('description', '')
            news.content = request.POST.get('content')
            news.is_published = request.POST.get('is_published') == 'on'
            news.save()
            _log_admin_action(request, f"Отредактировал новость #{news_id}: {news.title}")
            return redirect('/news/')
    except Exception as e:
        print(f"[news_edit] Error: {e}")
        return redirect('/news/')
    
    return render(request, 'news_form.html', {
        'active_page': 'news',
        'admin_name': _get_admin_name(request),
        'news': news,
        'title': 'Редактировать новость',
    })

def news_delete_view(request, news_id):
    """Удаление новости"""
    if not request.session.get('telegram_id'):
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    try:
        news = get_object_or_404(News, id=news_id)
        news.delete()
        _log_admin_action(request, f"Удалил новость #{news_id}")
    except Exception as e:
        print(f"[news_delete] Error: {e}")
    return JsonResponse({'success': True})


# ============= СОТРУДНИЧЕСТВО (АДМИНКА) =============

def partnership_list_view(request):
    """Список заявок на сотрудничество (админка)"""
    if not request.session.get('telegram_id'):
        return redirect('/')
    _log_page_view(request, 'Просмотр Заявок', 'Администратор открыл список заявок на сотрудничество')
    partnerships = []
    try:
        partnerships = Partnership.objects.all().order_by('-created_at')
    except Exception as e:
        print(f"[partnership_list] Error: {e}")
    return render(request, 'partnership_list.html', {
        'active_page': 'partnership',
        'admin_name': _get_admin_name(request),
        'partnerships': partnerships,
    })

def partnership_detail_view(request, partnership_id):
    """Детальный просмотр заявки с чатом (админка)"""
    if not request.session.get('telegram_id'):
        return redirect('/')
    _log_page_view(request, 'Просмотр Заявки', f'Администратор открыл заявку на сотрудничество #{partnership_id}')
    partnership = get_object_or_404(Partnership, id=partnership_id)
    messages = PartnershipMessage.objects.filter(partnership=partnership).order_by('created_at')
    
    if request.method == 'POST':
        data = json.loads(request.body)
        message_text = data.get('message')
        
        if message_text:
            admin_name = _get_admin_name(request)
            PartnershipMessage.objects.create(
                partnership=partnership,
                sender_type='admin',
                sender_name=admin_name,
                message=message_text
            )
            partnership.updated_at = timezone.now()
            partnership.save()
            _log_admin_action(request, f"Ответил в заявке на сотрудничество #{partnership_id}")
            return JsonResponse({'success': True})
    
    return render(request, 'partnership_detail.html', {
        'active_page': 'partnership',
        'admin_name': _get_admin_name(request),
        'partnership': partnership,
        'messages': messages,
    })

@csrf_exempt
def partnership_update_status(request, partnership_id):
    """Обновление статуса заявки"""
    if not request.session.get('telegram_id'):
        return JsonResponse({'error': 'Unauthorized'}, status=401)
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
    
    _log_admin_action(request, f"Обновил статус заявки на сотрудничество #{partnership_id} → {status_text}", target_id=partnership_id)
    return JsonResponse({'success': True})

@csrf_exempt
def partnership_delete_view(request, partnership_id):
    """Удаление заявки"""
    if not request.session.get('telegram_id'):
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    partnership = get_object_or_404(Partnership, id=partnership_id)
    partnership.delete()
    _log_admin_action(request, f"Удалил заявку на сотрудничество #{partnership_id}", target_id=partnership_id)
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