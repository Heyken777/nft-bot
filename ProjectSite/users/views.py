import json, os, sqlite3
from datetime import datetime, timedelta
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, '..', 'novixgift.db')


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def get_admin_name(request):
    if request.user.is_authenticated:
        return request.user.username
    return request.session.get('admin', 'Admin')


def log_admin_action(request, action: str, target_id=None, amount=None):
    admin_id = request.user.id if request.user.is_authenticated else 0
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO admin_logs (admin_id, action, target_id, amount) VALUES (?, ?, ?, ?)",
        (admin_id, action, target_id, amount)
    )
    conn.commit()
    conn.close()


# ===================== AUTH =====================

def login_view(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        username = data.get('username')
        password = data.get('password')
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            request.session['admin'] = username
            return JsonResponse({
                'success': True, 'token': 'django-session',
                'username': username, 'role': 'admin'
            })
        return JsonResponse({'success': False, 'error': 'Неверные данные'}, status=401)
    return render(request, 'login.html')


def logout_view(request):
    logout(request)
    return redirect('/')


# ===================== PAGES =====================

@login_required(login_url='/')
def dashboard_view(request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM users WHERE is_premium=1 AND (premium_until IS NULL OR premium_until > datetime('now'))")
    premium_users = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM deals WHERE status NOT IN ('completed','cancelled')")
    active_deals = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM disputes WHERE status='pending'")
    disputes = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM users WHERE created_at >= date('now', '-7 days')")
    new_users_week = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM deals WHERE created >= date('now', '-30 days')")
    deals_month = cur.fetchone()[0] or 0
    cur.execute("SELECT COALESCE(SUM(amount), 0) FROM deals WHERE status='completed'")
    revenue = cur.fetchone()[0] or 0
    conn.close()
    return render(request, 'dashboard.html', {
        'active_page': 'dashboard',
        'admin_name': get_admin_name(request),
        'total_users': total_users,
        'active_subs': premium_users,
        'revenue': revenue,
        'tickets_open': disputes,
        'new_users_week': new_users_week,
        'payments_month': deals_month,
    })


@login_required(login_url='/')
def users_view(request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT user_id, username, first_name, last_name,
               balance_RUB AS balance, created_at, is_blocked,
               is_premium
        FROM users ORDER BY created_at DESC
    """)
    rows = cur.fetchall()
    conn.close()
    user_list = []
    for u in rows:
        d = dict(u)
        d['telegram_id'] = d.pop('user_id')
        display = d.get('username') or d.get('first_name') or str(d['telegram_id'])
        d['display_name'] = display
        d['avatar_letter'] = display[0].upper()
        d['total_referrals'] = 0
        d['active_referrals'] = 0
        d['subscription_count'] = 0
        user_list.append(d)
    return render(request, 'users.html', {
        'active_page': 'users',
        'users': user_list,
    })


@login_required(login_url='/')
def user_detail_view(request, telegram_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (telegram_id,))
    user = cur.fetchone()
    cur.execute("SELECT * FROM deals WHERE buyer=? OR seller=? ORDER BY created DESC LIMIT 20",
                (telegram_id, telegram_id))
    deals = cur.fetchall()
    cur.execute("SELECT * FROM disputes WHERE opened_by=? ORDER BY created_at DESC LIMIT 10", (telegram_id,))
    disputes = cur.fetchall()
    cur.execute("SELECT * FROM users WHERE referred_by=?", (telegram_id,))
    referrals = cur.fetchall()
    cur.execute("SELECT * FROM users WHERE user_id=(SELECT referred_by FROM users WHERE user_id=?)",
                (telegram_id,))
    inviter = cur.fetchone()
    conn.close()

    user_dict = dict(user) if user else None
    if user_dict:
        user_dict['telegram_id'] = user_dict.pop('user_id')
        display = user_dict.get('username') or user_dict.get('first_name') or str(user_dict['telegram_id'])
        user_dict['display_name'] = display
        user_dict['avatar_letter'] = display[0].upper()

    return render(request, 'user_detail.html', {
        'active_page': 'users',
        'user': user_dict,
        'deals': [dict(d) for d in deals],
        'disputes': [dict(d) for d in disputes],
        'referrals': [dict(r) for r in referrals],
        'inviter': dict(inviter) if inviter else None,
        'invited_users': [dict(r) for r in referrals],
        'subscriptions': [],
        'operations': [],
        'messages': [],
        'tickets': [],
        'now': datetime.now(),
    })


@login_required(login_url='/')
def promocodes_view(request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT rowid, * FROM promocodes WHERE active=1 ORDER BY created_at DESC")
    promos = cur.fetchall()
    conn.close()
    promo_list = []
    for p in promos:
        d = dict(p)
        d['id'] = d['rowid']
        d['discount'] = d.get('amount', 0)
        d['type'] = 'fixed'
        d['used'] = d.get('used_count', 0)
        d['expiry_date'] = d.get('expires_at', '')
        promo_list.append(d)
    return render(request, 'promocodes.html', {
        'active_page': 'promocodes',
        'promocodes': promo_list,
    })


@login_required(login_url='/')
def broadcast_view(request):
    return render(request, 'broadcast.html', {'active_page': 'broadcast'})


@login_required(login_url='/')
def profile_view(request):
    admin_name = get_admin_name(request)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM admin_logs WHERE admin_id=? ORDER BY timestamp DESC LIMIT 50",
                (request.user.id,))
    logs = cur.fetchall()
    conn.close()
    return render(request, 'profile.html', {
        'active_page': 'profile',
        'admin_data': {'username': admin_name, 'role': 'Admin', 'custom_roles': []},
        'logs': [dict(l) for l in logs],
        'tickets_assigned': 0,
        'closed_tickets': 0,
        'rating': min(100, len(logs)),
        'viewing_self': True,
    })


# ===================== DISPUTES / ARBITRATION (Stage 5) =====================

@login_required(login_url='/')
def disputes_view(request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT d.*, de.seller, de.buyer, de.amount, de.currency, de.item, de.status AS deal_status
        FROM disputes d
        LEFT JOIN deals de ON d.deal_id = de.id
        ORDER BY d.created_at DESC
    """)
    disputes = cur.fetchall()
    conn.close()
    disputes_data = []
    for d in disputes:
        row = dict(d)
        disputes_data.append({
            'id': row.get('id'),
            'username': f"user_{row.get('buyer')}",
            'user_id': row.get('buyer'),
            'user_login': f"User {row.get('buyer')}",
            'subject': f"Спор по сделке #{row.get('deal_id')} — {row.get('item', 'NFT')}",
            'order_number': f"#{row.get('deal_id')}",
            'user_type': 'buyer' if row.get('initiator') == 'buyer' else 'seller',
            'status': 'open' if row.get('status') == 'pending' else row.get('status', 'closed'),
            'created_at': row.get('created_at', ''),
        })
    return render(request, 'tickets.html', {
        'active_page': 'tickets',
        'tickets': disputes_data,
    })


@csrf_exempt
@login_required(login_url='/')
def dispute_detail_api(request, dispute_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT d.*, de.seller, de.buyer, de.amount, de.currency, de.item, de.status AS deal_status,
               de.created AS deal_created, de.commission
        FROM disputes d
        LEFT JOIN deals de ON d.deal_id = de.id
        WHERE d.id=?
    """, (dispute_id,))
    dispute = cur.fetchone()
    conn.close()
    if not dispute:
        return JsonResponse({'error': 'Not found'}, status=404)
    return JsonResponse(dict(dispute))


@csrf_exempt
@require_http_methods(["POST"])
@login_required(login_url='/')
def dispute_resolve_api(request, dispute_id):
    data = json.loads(request.body)
    decision = data.get('decision')
    admin_name = get_admin_name(request)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM disputes WHERE id=?", (dispute_id,))
    dispute = cur.fetchone()
    if not dispute:
        conn.close()
        return JsonResponse({'error': 'Not found'}, status=404)
    deal_id = dispute['deal_id']
    cur.execute("SELECT * FROM deals WHERE id=?", (deal_id,))
    deal = cur.fetchone()
    if not deal:
        conn.close()
        return JsonResponse({'error': 'Deal not found'}, status=404)
    currency = deal['currency']
    amount = deal['amount']
    commission = deal['commission'] or 0

    if decision == 'seller':
        seller_payout = amount - commission
        cur.execute(f"UPDATE users SET balance_{currency} = COALESCE(balance_{currency},0) + ? WHERE user_id=?",
                    (seller_payout, deal['seller']))
        cur.execute("UPDATE deals SET status='completed', completed=datetime('now') WHERE id=?", (deal_id,))
        cur.execute("UPDATE disputes SET status='resolved_seller' WHERE id=?", (dispute_id,))
        action_desc = f"Спор #{dispute_id}: выплачено продавцу {seller_payout} {currency}"
    elif decision == 'buyer':
        cur.execute(f"UPDATE users SET balance_{currency} = COALESCE(balance_{currency},0) + ? WHERE user_id=?",
                    (amount, deal['buyer']))
        cur.execute("UPDATE deals SET status='cancelled', completed=datetime('now') WHERE id=?", (deal_id,))
        cur.execute("UPDATE disputes SET status='resolved_buyer' WHERE id=?", (dispute_id,))
        action_desc = f"Спор #{dispute_id}: возвращено покупателю {amount} {currency}"
    else:
        conn.close()
        return JsonResponse({'error': 'Invalid decision'}, status=400)

    log_admin_action(request, action_desc, target_id=deal['seller'])
    conn.commit()
    conn.close()
    return JsonResponse({'success': True, 'action': action_desc})


# ===================== API: PROMOCODES (Stage 3) =====================

@csrf_exempt
@require_http_methods(["POST"])
@login_required(login_url='/')
def api_create_promocode(request):
    data = json.loads(request.body)
    admin_name = get_admin_name(request)
    code = data.get('code', '').upper().strip()
    amount = data.get('discount', 0)
    max_uses = data.get('max_uses', 100)
    expiry_days = data.get('expiry_days')
    expires_at = None
    if expiry_days:
        expires_at = (datetime.now() + timedelta(days=int(expiry_days))).isoformat()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO promocodes (code, amount, max_uses, expires_at, active, created_by, created_at) VALUES (?, ?, ?, ?, 1, ?, datetime('now'))",
        (code, amount, max_uses, expires_at, request.user.id)
    )
    conn.commit()
    log_admin_action(request, f"Создал промокод {code} на {amount}")
    conn.close()
    return JsonResponse({'success': True})


@csrf_exempt
@require_http_methods(["POST"])
@login_required(login_url='/')
def api_update_promocode(request, promo_code):
    data = json.loads(request.body)
    code = data.get('code', '').upper().strip()
    amount = data.get('discount', 0)
    max_uses = data.get('max_uses', 100)
    expiry_days = data.get('expiry_days')
    expires_at = None
    if expiry_days:
        expires_at = (datetime.now() + timedelta(days=int(expiry_days))).isoformat()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE promocodes SET code=?, amount=?, max_uses=?, expires_at=? WHERE code=?",
        (code, amount, max_uses, expires_at, promo_code)
    )
    conn.commit()
    log_admin_action(request, f"Обновил промокод {code}")
    conn.close()
    return JsonResponse({'success': True})


@csrf_exempt
@require_http_methods(["DELETE"])
@login_required(login_url='/')
def api_delete_promocode(request, promo_code):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE promocodes SET active=0, deleted_by=?, deleted_at=datetime('now'), delete_reason='deleted_by_admin' WHERE code=?",
        (request.user.id, promo_code)
    )
    conn.commit()
    log_admin_action(request, f"Удалил промокод {promo_code}")
    conn.close()
    return JsonResponse({'success': True})


@csrf_exempt
@require_http_methods(["GET"])
@login_required(login_url='/')
def api_get_promocode(request, promo_code):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT rowid, * FROM promocodes WHERE code=?", (promo_code,))
    promo = cur.fetchone()
    conn.close()
    if not promo:
        return JsonResponse({'error': 'Not found'}, status=404)
    d = dict(promo)
    d['id'] = d['rowid']
    d['discount'] = d.get('amount', 0)
    d['type'] = 'fixed'
    d['used'] = d.get('used_count', 0)
    d['expiry_date'] = d.get('expires_at', '')
    return JsonResponse(d)


@csrf_exempt
@require_http_methods(["GET"])
@login_required(login_url='/')
def api_get_promocodes_list(request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT rowid, * FROM promocodes WHERE active=1 ORDER BY created_at DESC")
    promos = cur.fetchall()
    conn.close()
    result = []
    for p in promos:
        d = dict(p)
        d['id'] = d['rowid']
        d['discount'] = d.get('amount', 0)
        d['type'] = 'fixed'
        d['used'] = d.get('used_count', 0)
        d['expiry_date'] = d.get('expires_at', '')
        result.append(d)
    return JsonResponse(result, safe=False)


# ===================== API: BALANCE (Stage 4) =====================

VALID_CURRENCIES = {'RUB','USD','EUR','BYN','UAH','KZT','UZS','TON','USDT','STARS'}

@csrf_exempt
@require_http_methods(["POST"])
@login_required(login_url='/')
def api_change_balance(request, telegram_id):
    data = json.loads(request.body)
    amount = float(data.get('amount', 0))
    currency = data.get('currency', 'RUB').upper()
    if currency not in VALID_CURRENCIES:
        return JsonResponse({'success': False, 'error': f'Invalid currency: {currency}'}, status=400)
    reason = data.get('reason', '')
    admin_name = get_admin_name(request)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        f"UPDATE users SET balance_{currency} = COALESCE(balance_{currency},0) + ? WHERE user_id=?",
        (amount, telegram_id)
    )
    log_admin_action(request, f"{'Начислил' if amount>0 else 'Списал'} {abs(amount)} {currency} пользователю {telegram_id}: {reason}",
                     target_id=telegram_id, amount=amount)
    conn.commit()
    conn.close()
    return JsonResponse({'success': True})


@csrf_exempt
@require_http_methods(["POST"])
@login_required(login_url='/')
def api_send_message(request, telegram_id):
    data = json.loads(request.body)
    message = data.get('message', '')
    admin_name = get_admin_name(request)
    log_admin_action(request, f"Отправил сообщение пользователю {telegram_id}: {message[:50]}",
                     target_id=telegram_id)
    return JsonResponse({'success': True, 'note': 'Message logged. Integrate with bot for real delivery.'})


# ===================== API: PREMIUM (Stage 4) =====================

@csrf_exempt
@require_http_methods(["POST"])
@login_required(login_url='/')
def api_grant_premium(request, telegram_id):
    data = json.loads(request.body)
    days = int(data.get('days', 30))
    admin_name = get_admin_name(request)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT premium_until FROM users WHERE user_id=?", (telegram_id,))
    row = cur.fetchone()
    current_expiry = datetime.now()
    if row and row['premium_until']:
        try:
            current_expiry = datetime.fromisoformat(row['premium_until'].replace('Z', ''))
        except (ValueError, TypeError, AttributeError):
            current_expiry = datetime.now()

    if days <= 0:
        new_expiry = None
        is_premium = 0
        action = f"Забрал Premium у пользователя {telegram_id}"
    elif days >= 99999:
        new_expiry = '2099-12-31 23:59:59'
        is_premium = 1
        action = f"Выдал Premium навсегда пользователю {telegram_id}"
    else:
        new_expiry = (current_expiry + timedelta(days=days)).isoformat()
        is_premium = 1
        action = f"Выдал Premium на {days} дней пользователю {telegram_id}"

    cur.execute(
        "UPDATE users SET is_premium=?, premium_until=?, premium_granted_by=?, premium_granted_at=datetime('now'), premium_duration_days=? WHERE user_id=?",
        (is_premium, new_expiry, request.user.id, days, telegram_id)
    )
    conn.commit()
    log_admin_action(request, action, target_id=telegram_id)
    conn.close()
    return JsonResponse({'success': True})


# ===================== API: ADMIN LOGS / AUDIT =====================

@csrf_exempt
@require_http_methods(["GET"])
@login_required(login_url='/')
def api_get_audit_logs(request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM admin_logs ORDER BY timestamp DESC LIMIT 100")
    logs = cur.fetchall()
    conn.close()
    return JsonResponse([dict(l) for l in logs], safe=False)


@csrf_exempt
@require_http_methods(["POST"])
@login_required(login_url='/')
def api_clear_audit(request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM admin_logs")
    conn.commit()
    log_admin_action(request, "Очистил журнал аудита")
    conn.close()
    return JsonResponse({'success': True})


# ===================== API: OTHER =====================

@csrf_exempt
def api_login(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)
    data = json.loads(request.body)
    username = data.get('username')
    password = data.get('password')
    user = authenticate(request, username=username, password=password)
    if user:
        login(request, user)
        request.session['admin'] = username
        return JsonResponse({'success': True, 'token': 'django-session', 'username': username, 'role': 'admin'})
    return JsonResponse({'success': False, 'error': 'Неверные данные'}, status=401)


@csrf_exempt
@require_http_methods(["GET"])
@login_required(login_url='/')
def api_export_users(request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, first_name, last_name, balance_RUB, created_at FROM users ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    import csv
    from django.http import HttpResponse
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="users.csv"'
    writer = csv.writer(response)
    writer.writerow(['ID', 'Username', 'Имя', 'Фамилия', 'Баланс RUB', 'Дата регистрации'])
    for u in rows:
        writer.writerow([u['user_id'], u['username'], u['first_name'], u['last_name'], u['balance_RUB'], u['created_at']])
    return response


@csrf_exempt
@require_http_methods(["GET"])
@login_required(login_url='/')
def api_export_audit(request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM admin_logs ORDER BY timestamp DESC")
    logs = cur.fetchall()
    conn.close()
    import csv
    from django.http import HttpResponse
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="audit_logs.csv"'
    writer = csv.writer(response)
    writer.writerow(['Время', 'Admin ID', 'Действие', 'Цель', 'Сумма'])
    for l in logs:
        writer.writerow([l['timestamp'], l['admin_id'], l['action'], l['target_id'], l['amount']])
    return response
