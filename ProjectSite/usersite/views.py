import json, os, sqlite3, hashlib, hmac, random
from datetime import datetime
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, '..', 'novixgift.db')

OWNER_TELEGRAM_ID = 1803437347


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def check_auth(request):
    return request.session.get('user_id') is not None


def get_or_create_user(telegram_id, username=None):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (telegram_id,))
    user = cur.fetchone()
    if not user:
        cur.execute("INSERT INTO users (user_id, username) VALUES (?, ?)",
                    (telegram_id, username or str(telegram_id)))
        conn.commit()
        cur.execute("SELECT * FROM users WHERE user_id=?", (telegram_id,))
        user = cur.fetchone()
    conn.close()
    return dict(user)


def user_login_view(request):
    bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', 'NovixGiftBot')
    return render(request, 'usersite/login.html', {
        'bot_username': bot_username,
        'bot_link': f"https://t.me/{bot_username}",
    })


def telegram_auth_view(request):
    token = request.GET.get('token')
    code = request.GET.get('code')

    if token:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE auth_token=?", (token,))
        user = cur.fetchone()
        conn.close()
        if user:
            request.session['user_id'] = user['user_id']
            request.session['username'] = user.get('username', str(user['user_id']))
            return redirect('/usersite/dashboard/')

    if code and code.isdigit() and len(code) == 6:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM auth_codes WHERE code=? AND expires_at > datetime('now')", (code,))
        auth = cur.fetchone()
        if auth:
            user = get_or_create_user(auth['user_id'])
            request.session['user_id'] = user['user_id']
            request.session['username'] = user.get('username', str(user['user_id']))
            if user['user_id'] == OWNER_TELEGRAM_ID:
                from django.contrib.auth import login
                from django.contrib.auth.models import User as DjangoUser
                django_user, _ = DjangoUser.objects.get_or_create(
                    username=f"tg_{user['user_id']}",
                    defaults={'is_staff': True, 'is_superuser': True}
                )
                django_user.is_staff = True
                django_user.is_superuser = True
                django_user.save()
                login(request, django_user)
                request.session['admin'] = 'Heyken'
            cur.execute("DELETE FROM auth_codes WHERE code=?", (code,))
            conn.commit()
            conn.close()
            return redirect('/usersite/dashboard/')
        conn.close()

    return redirect('/usersite/login/')


@csrf_exempt
@require_http_methods(["POST"])
def request_code_api(request):
    data = json.loads(request.body)
    telegram_id = int(data.get('telegram_id', 0))
    if not telegram_id:
        return JsonResponse({'success': False, 'error': 'Invalid ID'}, status=400)
    code = f"{random.randint(100000, 999999)}"
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO auth_codes (user_id, code, expires_at) VALUES (?, ?, datetime('now', '+5 minutes'))",
        (telegram_id, code)
    )
    cur.execute(
        "INSERT INTO notifications (user_id, title, message) VALUES (?, 'Код авторизации', ?)",
        (telegram_id, f"🔐 Ваш код для входа на сайт: <b>{code}</b>\nДействителен 5 минут.\n\nВведите его на странице входа или отправьте боту команду /code {telegram_id}")
    )
    conn.commit()
    conn.close()
    return JsonResponse({'success': True, 'message': 'Код отправлен'})


def test_login(request):
    request.session['user_id'] = OWNER_TELEGRAM_ID
    request.session['username'] = 'Arkadiex'
    return redirect('/usersite/dashboard/')


def dashboard_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')

    user_id = request.session.get('user_id')
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = cur.fetchone()

    cur.execute("SELECT * FROM deals WHERE buyer=? OR seller=? ORDER BY created DESC LIMIT 5", (user_id, user_id))
    deals = cur.fetchall()

    cur.execute("SELECT COUNT(*) FROM deals WHERE buyer=? AND status='completed'", (user_id,))
    purchases = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM deals WHERE seller=? AND status='completed'", (user_id,))
    sales = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(amount), 0) FROM deals WHERE seller=? AND status='completed'", (user_id,))
    total_earned = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM users WHERE referred_by=?", (user_id,))
    referrals_count = cur.fetchone()[0]

    try:
        cur.execute("SELECT * FROM news WHERE is_published=1 ORDER BY created_at DESC LIMIT 5")
        news = cur.fetchall()
    except sqlite3.OperationalError:
        news = []

    try:
        cur.execute("SELECT * FROM promocodes WHERE active=1 AND (expires_at IS NULL OR expires_at > datetime('now')) AND used_count < max_uses ORDER BY created_at DESC LIMIT 3")
        promocodes = cur.fetchall()
    except:
        promocodes = []

    conn.close()

    is_premium = user and user['is_premium']
    premium_until = user and user['premium_until']
    premium_active = False
    if is_premium and premium_until:
        try:
            premium_active = datetime.fromisoformat(premium_until.replace('Z', '')) > datetime.now()
        except:
            premium_active = True
    elif is_premium:
        premium_active = True

    return render(request, 'usersite/dashboard.html', {
        'user': dict(user) if user else None,
        'deals': [dict(d) for d in deals],
        'purchases': purchases,
        'sales': sales,
        'total_earned': total_earned,
        'referrals_count': referrals_count,
        'premium_active': premium_active,
        'news': [dict(n) for n in news],
        'promocodes': [dict(p) for p in promocodes],
        'subscriptions': [],
        'subscriptions_count': 0,
        'has_active_subscription': False,
        'all_subscriptions_count': 0,
        'tickets_count': 0,
        'now': datetime.now(),
    })


def profile_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')

    user_id = request.session.get('user_id')
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = cur.fetchone()

    cur.execute("SELECT * FROM deals WHERE buyer=? OR seller=? ORDER BY created DESC LIMIT 20", (user_id, user_id))
    deals = cur.fetchall()

    cur.execute("SELECT * FROM users WHERE referred_by=?", (user_id,))
    referrals = cur.fetchall()

    conn.close()

    is_premium = user and user['is_premium']
    premium_until = user and user['premium_until']
    premium_active = False
    days_left = 0
    if is_premium and premium_until:
        try:
            expiry = datetime.fromisoformat(premium_until.replace('Z', ''))
            premium_active = expiry > datetime.now()
            days_left = max(0, (expiry - datetime.now()).days)
        except:
            premium_active = True
    elif is_premium:
        premium_active = True

    return render(request, 'usersite/profile.html', {
        'user': dict(user) if user else None,
        'deals': [dict(d) for d in deals],
        'referrals': [dict(r) for r in referrals],
        'premium_active': premium_active,
        'days_left': days_left,
        'subscriptions': [],
        'operations': [],
        'active_subscription': None,
        'now': datetime.now(),
    })


def logout_view(request):
    request.session.flush()
    return redirect('/usersite/login/')


def user_profile_redirect(request, user_id):
    return redirect(f'/users/{user_id}/')


# ============= TICKET PAGES =============

def user_tickets_view(request):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('/usersite/login/')
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM support_tickets WHERE user_id=? ORDER BY updated_at DESC", (user_id,))
    tickets = [dict(t) for t in cur.fetchall()]
    conn.close()
    return render(request, 'usersite/tickets.html', {'tickets': tickets})


def user_ticket_new_view(request):
    if not request.session.get('user_id'):
        return redirect('/usersite/login/')
    return render(request, 'usersite/ticket_new.html')


def user_ticket_detail_view(request, ticket_id):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('/usersite/login/')
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM support_tickets WHERE id=? AND user_id=?", (ticket_id, user_id))
    ticket = cur.fetchone()
    if not ticket:
        conn.close()
        return redirect('/usersite/tickets/')
    cur.execute("SELECT * FROM support_ticket_messages WHERE ticket_id=? ORDER BY created_at", (ticket_id,))
    messages = [dict(m) for m in cur.fetchall()]
    conn.close()
    return render(request, 'usersite/ticket_detail.html', {
        'ticket': dict(ticket),
        'messages': messages,
    })


# ============= TICKET API =============

@csrf_exempt
def create_ticket(request):
    user_id = request.session.get('user_id')
    if not user_id:
        return JsonResponse({'success': False, 'error': 'Not logged in'}, status=401)
    subject = request.POST.get('subject', '')
    message = request.POST.get('message', '')
    user_login = request.POST.get('user_login', '')
    order_number = request.POST.get('order_number', '')
    user_type = request.POST.get('user_type', 'buyer')
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO support_tickets (user_id, subject, category, user_type, order_number) VALUES (?,?,?,?,?)",
        (user_id, subject, subject, user_type, order_number)
    )
    ticket_id = cur.lastrowid
    cur.execute(
        "INSERT INTO support_ticket_messages (ticket_id, sender_type, sender_name, message) VALUES (?,'user',?,?)",
        (ticket_id, user_login or f'User {user_id}', message)
    )
    conn.commit()
    conn.close()
    return JsonResponse({'success': True, 'ticket_id': ticket_id})


@csrf_exempt
def add_ticket_reply(request, ticket_id):
    user_id = request.session.get('user_id')
    if not user_id:
        return JsonResponse({'success': False, 'error': 'Not logged in'}, status=401)
    data = json.loads(request.body)
    message = data.get('message', '').strip()
    if not message:
        return JsonResponse({'success': False, 'error': 'Пустое сообщение'}, status=400)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id, status FROM support_tickets WHERE id=?", (ticket_id,))
    ticket = cur.fetchone()
    if not ticket or ticket['user_id'] != user_id:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Ticket not found'}, status=404)
    if ticket['status'] == 'closed':
        conn.close()
        return JsonResponse({'success': False, 'error': 'Ticket closed'}, status=400)
    cur.execute(
        "INSERT INTO support_ticket_messages (ticket_id, sender_type, sender_name, message) VALUES (?,'user',?,?)",
        (ticket_id, f'User {user_id}', message)
    )
    cur.execute("UPDATE support_tickets SET updated_at=datetime('now') WHERE id=?", (ticket_id,))
    conn.commit()
    conn.close()
    return JsonResponse({'success': True})


@csrf_exempt
def close_ticket(request, ticket_id):
    user_id = request.session.get('user_id')
    if not user_id:
        return JsonResponse({'success': False, 'error': 'Not logged in'}, status=401)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM support_tickets WHERE id=?", (ticket_id,))
    ticket = cur.fetchone()
    if not ticket or ticket['user_id'] != user_id:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Ticket not found'}, status=404)
    cur.execute("UPDATE support_tickets SET status='closed', updated_at=datetime('now') WHERE id=?", (ticket_id,))
    conn.commit()
    conn.close()
    return JsonResponse({'success': True})


@csrf_exempt
def change_ticket_status(request, ticket_id):
    return JsonResponse({'success': False, 'error': 'Use admin panel for status changes'})


@csrf_exempt
def assign_ticket(request, ticket_id):
    return JsonResponse({'success': False, 'error': 'Use admin panel for assignment'})
