import json, os, sqlite3
from functools import wraps
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.conf import settings

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, '..', 'novixgift.db')
OWNER_TELEGRAM_ID = int(os.getenv("OWNER_TELEGRAM_ID", "1803437347"))


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=40)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=40000;")
    conn.row_factory = sqlite3.Row
    return conn


def get_admin_name(request):
    return request.session.get('username') or request.session.get('admin', 'Admin')


def log_admin_action(request, action: str, target_id=None, amount=None):
    admin_id = request.session.get('telegram_id', 0)
    admin_name = request.session.get('username', '') or 'Admin'
    ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', '0.0.0.0')).split(',')[0].strip()
    now = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        conn = get_db()
        cur = conn.cursor()
        desc = f"CEO / Владелец {admin_name} совершил действие: {action}" if admin_id == OWNER_TELEGRAM_ID else action
        if target_id:
            desc += f" | target={target_id}"
        if amount:
            desc += f" | amount={amount}"
        desc += f" | IP: {ip}"
        cur.execute(
            "INSERT INTO audit_logs (timestamp, user_id, username, action_type, description, ip_address) VALUES (?, ?, ?, ?, ?, ?)",
            (now, admin_id, admin_name, action, desc, ip)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def log_page_view(request, action_type: str, description: str, target_id=None):
    admin_id = request.session.get('telegram_id', 0)
    admin_name = request.session.get('username', '') or 'Admin'
    ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', '0.0.0.0')).split(',')[0].strip()
    now = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        conn = get_db()
        cur = conn.cursor()
        desc = f"👑 {description} | IP: {ip}" if admin_id == OWNER_TELEGRAM_ID else f"{description} | IP: {ip}"
        if target_id:
            desc += f" | id={target_id}"
        cur.execute(
            "INSERT INTO audit_logs (timestamp, user_id, username, action_type, description, ip_address) VALUES (?, ?, ?, ?, ?, ?)",
            (now, admin_id, admin_name, action_type, desc, ip)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


ROLE_PERMISSIONS = {
    'CEO': ['*'],
    'Admin': [
        'dashboard', 'users', 'users_edit', 'deals', 'withdrawals',
        'promocodes', 'broadcast', 'disputes', 'audit',
        'tickets', 'tickets_reply', 'news', 'partnership',
    ],
    'Moderator': [
        'dashboard', 'users', 'deals', 'disputes', 'reviews',
        'tickets', 'tickets_reply', 'news',
    ],
    'Support': [
        'tickets', 'tickets_reply', 'users',
    ],
    'Analyst': [
        'dashboard', 'users', 'deals', 'audit',
    ],
}


def get_user_role(request):
    tid = request.session.get('telegram_id')
    if tid == OWNER_TELEGRAM_ID:
        return 'CEO'
    return request.session.get('admin_role', 'Admin')


def has_permission(request, permission):
    role = get_user_role(request)
    perms = ROLE_PERMISSIONS.get(role, [])
    if '*' in perms:
        return True
    return permission in perms


def require_permission(permission):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapper(request, *args, **kwargs):
            if not request.session.get('telegram_id'):
                return redirect('/')
            if not has_permission(request, permission):
                return render(request, 'no_access.html', {
                    'active_page': '',
                    'admin_name': get_admin_name(request),
                }, status=403)
            return view_func(request, *args, **kwargs)
        return _wrapper
    return decorator


# ===================== TICKETS VIEWS =====================

@require_permission('tickets')
def admin_tickets_view(request):
    log_page_view(request, 'Просмотр Тикетов', 'Администратор открыл список тикетов поддержки')
    tickets_data = []
    PRIORITY_MAP = {
        'vip': {'label': 'Критический', 'class': 'priority-critical', 'order': 4},
        'platinum': {'label': 'Высокий', 'class': 'priority-high', 'order': 3},
        'premium': {'label': 'Средний', 'class': 'priority-medium', 'order': 2},
    }
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT t.*, u.premium_tier
            FROM support_tickets t
            LEFT JOIN users u ON t.user_id = u.user_id
            ORDER BY
                CASE lower(u.premium_tier)
                    WHEN 'vip' THEN 0
                    WHEN 'platinum' THEN 1
                    WHEN 'premium' THEN 2
                    ELSE 3
                END,
                t.updated_at DESC
        """)
        user_cache = {}
        for t in cur.fetchall():
            d = dict(t)
            uid = d.get('user_id')
            if uid not in user_cache:
                cur.execute("SELECT username FROM users WHERE user_id=?", (uid,))
                urow = cur.fetchone()
                user_cache[uid] = urow[0] if urow else str(uid)
            display_name = user_cache[uid]
            tier = (d.get('premium_tier') or 'free').lower()
            p = PRIORITY_MAP.get(tier, {'label': 'Низкий', 'class': 'priority-low', 'order': 1})
            tickets_data.append({
                'id': d.get('id'),
                'user_id': uid,
                'username': display_name,
                'user_login': display_name,
                'subject': d.get('subject', 'Без темы'),
                'order_number': d.get('order_number', '—'),
                'user_type': d.get('user_type', '—'),
                'status': d.get('status', 'open'),
                'created_at': d.get('created_at', ''),
                'priority': p['label'],
                'priority_class': p['class'],
                'priority_order': p['order'],
                'assigned_to': d.get('assigned_to') or '—',
            })
        conn.close()
    except Exception as e:
        print(f"[admin_tickets] Error: {e}")
    return render(request, 'tickets.html', {
        'active_page': 'admin_tickets',
        'admin_name': get_admin_name(request),
        'tickets': tickets_data,
    })


@require_permission('tickets')
def admin_ticket_detail_view(request, ticket_id):
    log_page_view(request, 'Просмотр Тикета', f'Администратор открыл тикет #{ticket_id}', target_id=ticket_id)
    PRIORITY_MAP = {
        'vip': {'label': 'Критический', 'class': 'priority-critical', 'order': 4},
        'platinum': {'label': 'Высокий', 'class': 'priority-high', 'order': 3},
        'premium': {'label': 'Средний', 'class': 'priority-medium', 'order': 2},
    }
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT t.*, u.premium_tier
            FROM support_tickets t
            LEFT JOIN users u ON t.user_id = u.user_id
            WHERE t.id=?
        """, (ticket_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return redirect('/tickets/')
        ticket = dict(row)
        uid = ticket.get('user_id')
        cur.execute("SELECT username FROM users WHERE user_id=?", (uid,))
        urow = cur.fetchone()
        ticket['user_login'] = urow[0] if urow else str(uid)
        tier = (ticket.get('premium_tier') or 'free').lower()
        p = PRIORITY_MAP.get(tier, {'label': 'Низкий', 'class': 'priority-low', 'order': 1})
        ticket['priority'] = p['label']
        ticket['priority_class'] = p['class']
        cur.execute("SELECT * FROM support_ticket_messages WHERE ticket_id=? ORDER BY created_at", (ticket_id,))
        messages = []
        for m in cur.fetchall():
            md = dict(m)
            raw = (md.get('attachments') or '').strip()
            md['attachments_list'] = [a for a in raw.split(',') if a] if raw else []
            messages.append(md)
        cur.execute("SELECT user_id, username, admin_role FROM users WHERE admin_role IS NOT NULL AND admin_role != '' ORDER BY username")
        admins = [dict(a) for a in cur.fetchall()]
        conn.close()
        return render(request, 'ticket_detail_admin.html', {
            'active_page': 'admin_tickets',
            'admin_name': get_admin_name(request),
            'ticket': ticket,
            'messages': messages,
            'admins': admins,
        })
    except Exception as e:
        print(f"[admin_ticket_detail] Error: {e}")
    return redirect('/tickets/')


@csrf_exempt
@require_http_methods(["POST"])
@require_permission('tickets_reply')
def admin_ticket_reply_api(request, ticket_id):
    try:
        data = json.loads(request.body)
        message = data.get('message', '').strip()
        if not message:
            return JsonResponse({'success': False, 'error': 'Пустое сообщение'}, status=400)
        is_ceo = request.session.get('telegram_id') == OWNER_TELEGRAM_ID
        admin_name = get_admin_name(request)
        admin_uid = request.session.get('telegram_id', 0)
        conn2 = get_db()
        cur2 = conn2.cursor()
        cur2.execute("SELECT ticket_role FROM users WHERE user_id=?", (admin_uid,))
        trow = cur2.fetchone()
        sender_role = 'CEO' if is_ceo else (trow[0] if trow and trow[0] else 'Администратор')
        conn2.close()
        display_message = message
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO support_ticket_messages (ticket_id, sender_type, sender_name, message, sender_role) VALUES (?,'admin',?,?,?)",
            (ticket_id, admin_name, display_message, sender_role)
        )
        cur.execute("UPDATE support_tickets SET updated_at=datetime('now') WHERE id=?", (ticket_id,))
        cur.execute("SELECT user_id FROM support_tickets WHERE id=?", (ticket_id,))
        row = cur.fetchone()
        if row:
            cur.execute(
                "INSERT INTO notifications (user_id, title, message) VALUES (?, 'Новый ответ в тикете', ?)",
                (row['user_id'], '🔔 В вашем тикете на сайте появился новый ответ от поддержки!')
            )
        conn.commit()
        conn.close()
        return JsonResponse({'success': True})
    except Exception as e:
        print(f"[ticket_reply] Error: {e}")
        return JsonResponse({'success': False, 'error': 'Server error'}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@require_permission('tickets')
def admin_ticket_status_api(request, ticket_id):
    try:
        data = json.loads(request.body)
        new_status = data.get('status', 'open')
        if new_status not in ('open', 'in_progress', 'closed'):
            return JsonResponse({'success': False, 'error': 'Invalid status'}, status=400)
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT status FROM support_tickets WHERE id=?", (ticket_id,))
        old_row = cur.fetchone()
        old_status = old_row[0] if old_row else 'unknown'
        cur.execute("UPDATE support_tickets SET status=?, updated_at=datetime('now') WHERE id=?", (new_status, ticket_id))
        admin_name = get_admin_name(request)
        status_labels = {'open': '🟢 Открыт', 'in_progress': '🟡 В работе', 'closed': '🔴 Закрыт'}
        new_label = status_labels.get(new_status, new_status)
        sys_msg = f"📋 Статус заявки изменён на «{new_label}» — {admin_name}"
        cur.execute(
            "INSERT INTO support_ticket_messages (ticket_id, sender_type, sender_name, message, sender_role) VALUES (?,'system',?,?,?)",
            (ticket_id, 'Система', sys_msg, '')
        )
        conn.commit()
        conn.close()
        log_admin_action(request, f"Изменил статус тикета #{ticket_id}: {old_status} → {new_status}", target_id=ticket_id)
        return JsonResponse({'success': True})
    except Exception as e:
        print(f"[ticket_status] Error: {e}")
        return JsonResponse({'success': False, 'error': 'Server error'}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@require_permission('tickets')
def admin_ticket_assign_api(request, ticket_id):
    try:
        data = json.loads(request.body)
        assigned_to = data.get('assigned_to', '')
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT assigned_to FROM support_tickets WHERE id=?", (ticket_id,))
        old_row = cur.fetchone()
        old_assign = old_row[0] if old_row else ''
        cur.execute("UPDATE support_tickets SET assigned_to=?, updated_at=datetime('now') WHERE id=?", (assigned_to, ticket_id))
        admin_name = get_admin_name(request)
        if assigned_to:
            sys_msg = f"📌 {admin_name} назначил ответственным — {assigned_to}"
        else:
            sys_msg = f"📌 {admin_name} снял назначение с {old_assign}"
        cur.execute(
            "INSERT INTO support_ticket_messages (ticket_id, sender_type, sender_name, message, sender_role) VALUES (?,'system',?,?,?)",
            (ticket_id, 'Система', sys_msg, '')
        )
        conn.commit()
        conn.close()
        action = f"Назначил тикет #{ticket_id}: {old_assign or '—'} → {assigned_to or '—'}"
        log_admin_action(request, action, target_id=ticket_id)
        return JsonResponse({'success': True})
    except Exception as e:
        print(f"[ticket_assign] Error: {e}")
        return JsonResponse({'success': False, 'error': 'Server error'}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@require_permission('tickets')
def admin_ticket_close_api(request, ticket_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE support_tickets SET status='closed', updated_at=datetime('now') WHERE id=?", (ticket_id,))
        conn.commit()
        conn.close()
        admin_name = get_admin_name(request)
        log_admin_action(request, f"Закрыл тикет #{ticket_id}", target_id=ticket_id)
        return JsonResponse({'success': True, 'admin_name': admin_name})
    except Exception as e:
        print(f"[ticket_close] Error: {e}")
        return JsonResponse({'success': False, 'error': 'Server error'}, status=500)
