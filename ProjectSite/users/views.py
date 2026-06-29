import json, os, sqlite3, requests
from datetime import datetime, timedelta
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.conf import settings

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
    cur.execute("SELECT COUNT(*) FROM deals WHERE status='disputed'")
    disputes = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM users WHERE created_at >= date('now', '-7 days')")
    new_users_week = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM deals WHERE created >= date('now', '-30 days')")
    deals_month = cur.fetchone()[0] or 0
    cur.execute("SELECT COALESCE(SUM(amount), 0) FROM deals WHERE status='completed'")
    revenue = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(*) FROM deals WHERE status='completed'")
    completed_deals = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM deals WHERE status='cancelled'")
    cancelled_deals = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM deals WHERE status='awaiting'")
    awaiting_deals = cur.fetchone()[0] or 0
    cur.execute("SELECT COALESCE(SUM(amount), 0) FROM deals WHERE status='completed' AND created >= date('now', '-7 days')")
    revenue_week = cur.fetchone()[0] or 0

    # Данные для графиков за 7 дней
    revenue_labels = []
    revenue_data = []
    users_labels = []
    users_data = []
    for i in range(6, -1, -1):
        day = (datetime.now() - timedelta(days=i)).strftime('%d.%m')
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM deals WHERE status='completed' AND date(created) = date('now', ?)", (f'-{i} days',))
        rev = cur.fetchone()[0] or 0
        revenue_labels.append(day)
        revenue_data.append(rev)
        cur.execute("SELECT COUNT(*) FROM users WHERE date(created_at) = date('now', ?)", (f'-{i} days',))
        usr = cur.fetchone()[0] or 0
        users_labels.append(day)
        users_data.append(usr)

    # Последние пользователи
    cur.execute("SELECT user_id, username, balance_RUB, created_at, is_premium FROM users ORDER BY created_at DESC LIMIT 5")
    recent_users = [dict(r) for r in cur.fetchall()]

    # Последние сделки
    cur.execute("SELECT * FROM deals ORDER BY created DESC LIMIT 5")
    recent_deals = [dict(d) for d in cur.fetchall()]

    # Активные споры
    try:
        cur.execute("SELECT * FROM disputes WHERE status='pending' ORDER BY created_at DESC LIMIT 5")
        pending_disputes = [dict(d) for d in cur.fetchall()]
    except:
        pending_disputes = []

    # Открытые тикеты
    try:
        cur.execute("SELECT COUNT(*) FROM support_tickets WHERE status='open'")
        open_tickets = cur.fetchone()[0] or 0
    except:
        open_tickets = 0

    conversion = round(premium_users / total_users * 100, 1) if total_users else 0

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
        'active_deals': active_deals,
        'completed_deals': completed_deals,
        'cancelled_deals': cancelled_deals,
        'awaiting_deals': awaiting_deals,
        'revenue_week': revenue_week,
        'revenue_labels': json.dumps(revenue_labels),
        'revenue_data': json.dumps(revenue_data),
        'users_labels': json.dumps(users_labels),
        'users_data': json.dumps(users_data),
        'conversion': conversion,
        'recent_users': recent_users,
        'recent_deals': recent_deals,
        'pending_disputes': pending_disputes,
        'open_tickets': open_tickets,
    })


@login_required(login_url='/')
def users_view(request):
    conn = get_db()
    cur = conn.cursor()
    search = request.GET.get('q', '').strip()
    page = int(request.GET.get('page', 1))
    per_page = 50
    offset = (page - 1) * per_page

    if search:
        cur.execute("""
            SELECT user_id, username, balance_RUB AS balance, created_at, 0 AS is_blocked, is_premium
            FROM users WHERE username LIKE ? OR CAST(user_id AS TEXT) LIKE ?
            ORDER BY created_at DESC LIMIT ? OFFSET ?
        """, (f'%{search}%', f'%{search}%', per_page, offset))
        cur.execute("SELECT COUNT(*) FROM users WHERE username LIKE ? OR CAST(user_id AS TEXT) LIKE ?",
                    (f'%{search}%', f'%{search}%'))
    else:
        cur.execute("""
            SELECT user_id, username, balance_RUB AS balance, created_at, 0 AS is_blocked, is_premium
            FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?
        """, (per_page, offset))
        cur.execute("SELECT COUNT(*) FROM users")
    total = cur.fetchone()[0] or 0
    rows = cur.fetchall()
    conn.close()
    user_list = []
    for u in rows:
        d = dict(u)
        d['telegram_id'] = d.pop('user_id')
        display = d.get('username') or str(d['telegram_id'])
        d['display_name'] = display
        d['avatar_letter'] = display[0].upper()
        user_list.append(d)
    return render(request, 'users.html', {
        'active_page': 'users',
        'users': user_list,
        'search': search,
        'page': page,
        'total_pages': max(1, (total + per_page - 1) // per_page),
        'total': total,
    })


CURRENCY_LIST = ['RUB','USD','EUR','BYN','UAH','KZT','UZS','TON','USDT','STARS']
CURRENCY_RATES = {'RUB':1,'USD':90,'EUR':95,'BYN':28,'UAH':2.3,'KZT':0.19,'UZS':0.0075,'TON':500,'USDT':90,'STARS':1.5}

@login_required(login_url='/')
def user_detail_view(request, telegram_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (telegram_id,))
    user = cur.fetchone()
    if not user:
        conn.close()
        return render(request, 'user_detail.html', {'active_page':'users','user':None,'now':datetime.now()})

    cur.execute("SELECT * FROM deals WHERE buyer=? OR seller=? ORDER BY created DESC LIMIT 20", (telegram_id, telegram_id))
    deals = cur.fetchall()
    cur.execute("SELECT * FROM disputes WHERE opened_by=? ORDER BY created_at DESC LIMIT 10", (telegram_id,))
    disputes = cur.fetchall()
    cur.execute("SELECT * FROM users WHERE referred_by=?", (telegram_id,))
    referrals = cur.fetchall()
    cur.execute("SELECT * FROM users WHERE user_id=(SELECT referred_by FROM users WHERE user_id=?)", (telegram_id,))
    inviter = cur.fetchone()
    cur.execute("SELECT * FROM admin_logs WHERE target_id=? ORDER BY timestamp DESC LIMIT 50", (telegram_id,))
    admin_logs = cur.fetchall()
    cur.execute("SELECT * FROM user_balance_backups WHERE user_id=? AND restored=0 ORDER BY backed_up_at DESC LIMIT 1", (telegram_id,))
    latest_backup = cur.fetchone()
    cur.execute("SELECT * FROM support_tickets WHERE user_id=? ORDER BY created_at DESC LIMIT 10", (telegram_id,))
    tickets = cur.fetchall()
    cur.execute("SELECT r.*, d.item AS deal_item FROM reviews r LEFT JOIN deals d ON r.deal_id=d.id WHERE r.reviewed_id=? ORDER BY r.created_at DESC LIMIT 20", (telegram_id,))
    reviews = cur.fetchall()
    cur.execute("SELECT AVG(rating) FROM reviews WHERE reviewed_id=?", (telegram_id,))
    avg_rating = cur.fetchone()[0] or 0
    conn.close()

    user_dict = dict(user)
    user_dict['telegram_id'] = user_dict.pop('user_id')
    display = user_dict.get('username') or str(user_dict['telegram_id'])
    user_dict['display_name'] = display
    user_dict['avatar_letter'] = display[0].upper()

    balances = {}
    total_rub = 0
    for c in CURRENCY_LIST:
        val = user_dict.get(f'balance_{c}', 0) or 0
        balances[c] = val
        total_rub += val * CURRENCY_RATES.get(c, 0)
    user_dict['balances'] = balances
    user_dict['total_rub'] = round(total_rub, 2)

    premium_active = False
    if user_dict.get('is_premium') and user_dict.get('premium_until'):
        try:
            premium_until = datetime.fromisoformat(user_dict['premium_until'].replace('Z',''))
            premium_active = premium_until > datetime.now()
        except:
            pass

    return render(request, 'user_detail.html', {
        'active_page': 'users',
        'user': user_dict,
        'deals': [dict(d) for d in deals],
        'disputes': [dict(d) for d in disputes],
        'referrals': [dict(r) for r in referrals],
        'inviter': dict(inviter) if inviter else None,
        'invited_users': [dict(r) for r in referrals],
        'admin_logs': [dict(l) for l in admin_logs],
        'latest_backup': dict(latest_backup) if latest_backup else None,
        'tickets': [dict(t) for t in tickets],
        'reviews': [dict(r) for r in reviews],
        'avg_rating': round(avg_rating, 1),
        'premium_active': premium_active,
        'now': datetime.now(),
        'currencies': CURRENCY_LIST,
    })


@login_required(login_url='/')
def deals_list_view(request):
    conn = get_db()
    cur = conn.cursor()
    search = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '').strip()
    page = int(request.GET.get('page', 1))
    per_page = 50
    offset = (page - 1) * per_page

    conditions = []
    params = []
    if search:
        conditions.append("(CAST(id AS TEXT) LIKE ? OR item LIKE ?)")
        params.extend([f'%{search}%', f'%{search}%'])
    if status_filter:
        conditions.append("status=?")
        params.append(status_filter)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    cur.execute(f"SELECT COUNT(*) FROM deals {where}", params)
    total = cur.fetchone()[0] or 0

    cur.execute(f"SELECT * FROM deals {where} ORDER BY created DESC LIMIT ? OFFSET ?", params + [per_page, offset])
    deals = cur.fetchall()
    conn.close()

    statuses = ['awaiting', 'completed', 'cancelled', 'disputed']
    return render(request, 'deals_list.html', {
        'active_page': 'deals',
        'deals': [dict(d) for d in deals],
        'search': search,
        'status_filter': status_filter,
        'statuses': statuses,
        'page': page,
        'total_pages': max(1, (total + per_page - 1) // per_page),
        'total': total,
    })


@login_required(login_url='/')
def withdrawals_view(request):
    conn = get_db()
    cur = conn.cursor()
    status_filter = request.GET.get('status', '').strip()
    page = int(request.GET.get('page', 1))
    per_page = 50
    offset = (page - 1) * per_page
    conditions = []
    params = []
    if status_filter:
        conditions.append("status=?")
        params.append(status_filter)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    cur.execute(f"SELECT COUNT(*) FROM withdrawal_requests {where}", params)
    total = cur.fetchone()[0] or 0
    cur.execute(f"SELECT * FROM withdrawal_requests {where} ORDER BY created_at DESC LIMIT ? OFFSET ?", params + [per_page, offset])
    requests = cur.fetchall()
    conn.close()
    return render(request, 'withdrawals.html', {
        'active_page': 'withdrawals',
        'requests': [dict(r) for r in requests],
        'status_filter': status_filter,
        'page': page,
        'total_pages': max(1, (total + per_page - 1) // per_page),
        'total': total,
    })


@csrf_exempt
@require_http_methods(["POST"])
@login_required(login_url='/')
def withdrawal_approve_api(request, req_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM withdrawal_requests WHERE id=?", (req_id,))
    req = cur.fetchone()
    if not req:
        conn.close()
        return JsonResponse({'error': 'Not found'}, status=404)
    cur.execute("UPDATE withdrawal_requests SET status='approved' WHERE id=?", (req_id,))
    cur.execute("UPDATE users SET balance_RUB = balance_RUB - ? WHERE user_id=?", (req['amount'], req['user_id']))
    conn.commit()
    conn.close()
    log_admin_action(request, f"withdrawal_approve", req['user_id'], req['amount'])
    return JsonResponse({'success': True})


@csrf_exempt
@require_http_methods(["POST"])
@login_required(login_url='/')
def withdrawal_reject_api(request, req_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE withdrawal_requests SET status='rejected' WHERE id=?", (req_id,))
    conn.commit()
    conn.close()
    return JsonResponse({'success': True})


@login_required(login_url='/')
def promocodes_view(request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT rowid, * FROM promocodes ORDER BY created_at DESC")
    promos = cur.fetchall()
    conn.close()
    now = datetime.now()
    promo_list = []
    for p in promos:
        d = dict(p)
        d['id'] = d['rowid']
        d['discount'] = d.get('amount', 0)
        d['type'] = 'fixed'
        d['used'] = d.get('used_count', 0)
        d['expiry_date'] = d.get('expires_at', '')
        active_flag = d.get('active', 0)
        if not active_flag:
            d['status_label'] = 'Неактивен'
            d['status_class'] = 'badge-expired'
        elif d.get('expires_at') and d['expires_at'] < now.strftime('%Y-%m-%d %H:%M:%S'):
            d['status_label'] = 'Истёк'
            d['status_class'] = 'badge-expired'
        else:
            d['status_label'] = 'Активен'
            d['status_class'] = 'badge-active'
        promo_list.append(d)
    return render(request, 'promocodes.html', {
        'active_page': 'promocodes',
        'promocodes': promo_list,
        'now': now,
    })


@login_required(login_url='/')
def broadcast_view(request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM newsletters ORDER BY created_at DESC LIMIT 20")
    newsletters = cur.fetchall()
    conn.close()
    return render(request, 'broadcast.html', {
        'active_page': 'broadcast',
        'newsletters': [dict(n) for n in newsletters],
    })


@csrf_exempt
@require_http_methods(["POST"])
@login_required(login_url='/')
def api_broadcast_send(request):
    data = json.loads(request.body)
    title = data.get('title', '')
    message = data.get('message', '')
    photo_url = data.get('photo_url', '')
    full_text = f"<b>{title}</b>\n\n{message}" if title else message

    bot_token = os.getenv('BOT_TOKEN') or getattr(settings, 'BOT_TOKEN', '')
    if not bot_token:
        return JsonResponse({'success': False, 'error': 'BOT_TOKEN not configured'})

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()

    sent = 0
    failed = 0
    for u in users:
        uid = u[0]
        try:
            if photo_url:
                requests.post(
                    f"https://api.telegram.org/bot{bot_token}/sendPhoto",
                    json={'chat_id': uid, 'photo': photo_url, 'caption': full_text, 'parse_mode': 'HTML'},
                    timeout=10
                )
            else:
                requests.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={'chat_id': uid, 'text': full_text, 'parse_mode': 'HTML'},
                    timeout=10
                )
            sent += 1
        except Exception as e:
            failed += 1

    cur.execute(
        "INSERT INTO newsletters (title, message, sent_count, created_by) VALUES (?, ?, ?, ?)",
        (title, message, sent, request.user.id)
    )
    conn.commit()
    conn.close()

    log_admin_action(request, f"broadcast_sent to {sent} users", amount=sent)
    return JsonResponse({'success': True, 'sent': sent, 'failed': failed})


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
    cur.execute("SELECT rowid, * FROM promocodes ORDER BY created_at DESC")
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
    cur.execute("SELECT user_id, username, balance_RUB, created_at FROM users ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    import csv
    from django.http import HttpResponse
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="users.csv"'
    writer = csv.writer(response)
    writer.writerow(['ID', 'Username', 'Баланс RUB', 'Дата регистрации'])
    for u in rows:
        writer.writerow([u['user_id'], u['username'], u['balance_RUB'], u['created_at']])
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


# ===================== ADMIN TICKETS =====================

@login_required(login_url='/')
def admin_tickets_view(request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM support_tickets ORDER BY updated_at DESC")
    rows = cur.fetchall()
    conn.close()
    tickets_data = []
    for t in rows:
        d = dict(t)
        tickets_data.append({
            'id': d['id'],
            'user_id': d['user_id'],
            'username': f"User {d['user_id']}",
            'user_login': f"User {d['user_id']}",
            'subject': d.get('subject', 'Без темы'),
            'order_number': d.get('order_number', '—'),
            'user_type': d.get('user_type', '—'),
            'status': d['status'],
            'created_at': d.get('created_at', ''),
        })
    return render(request, 'tickets.html', {
        'active_page': 'admin_tickets',
        'tickets': tickets_data,
        'admin_name': get_admin_name(request),
    })


@login_required(login_url='/')
def admin_ticket_detail_view(request, ticket_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM support_tickets WHERE id=?", (ticket_id,))
    ticket = cur.fetchone()
    if not ticket:
        conn.close()
        return redirect('/tickets/')
    cur.execute("SELECT * FROM support_ticket_messages WHERE ticket_id=? ORDER BY created_at", (ticket_id,))
    messages = [dict(m) for m in cur.fetchall()]
    conn.close()
    return render(request, 'ticket_detail_admin.html', {
        'active_page': 'admin_tickets',
        'ticket': dict(ticket),
        'messages': messages,
        'admin_name': get_admin_name(request),
    })


@csrf_exempt
@require_http_methods(["POST"])
@login_required(login_url='/')
def admin_ticket_reply_api(request, ticket_id):
    data = json.loads(request.body)
    message = data.get('message', '').strip()
    if not message:
        return JsonResponse({'success': False, 'error': 'Пустое сообщение'}, status=400)
    admin_name = get_admin_name(request)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO support_ticket_messages (ticket_id, sender_type, sender_name, message) VALUES (?,'admin',?,?)",
        (ticket_id, admin_name, message)
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


@csrf_exempt
@require_http_methods(["POST"])
@login_required(login_url='/')
def admin_ticket_status_api(request, ticket_id):
    data = json.loads(request.body)
    new_status = data.get('status', 'open')
    if new_status not in ('open', 'in_progress', 'closed'):
        return JsonResponse({'success': False, 'error': 'Invalid status'}, status=400)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE support_tickets SET status=?, updated_at=datetime('now') WHERE id=?", (new_status, ticket_id))
    conn.commit()
    conn.close()
    return JsonResponse({'success': True})


@csrf_exempt
@require_http_methods(["POST"])
@login_required(login_url='/')
def admin_ticket_assign_api(request, ticket_id):
    data = json.loads(request.body)
    assigned_to = data.get('assigned_to', '')
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE support_tickets SET assigned_to=?, updated_at=datetime('now') WHERE id=?", (assigned_to, ticket_id))
    conn.commit()
    conn.close()
    return JsonResponse({'success': True})


@csrf_exempt
@require_http_methods(["POST"])
@login_required(login_url='/')
def admin_ticket_close_api(request, ticket_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE support_tickets SET status='closed', updated_at=datetime('now') WHERE id=?", (ticket_id,))
    conn.commit()
    conn.close()
    return JsonResponse({'success': True})


# ===================== BALANCE BACKUP / RESTORE =====================

@csrf_exempt
@require_http_methods(["POST"])
@login_required(login_url='/')
def api_backup_balance(request, telegram_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (telegram_id,))
    user = cur.fetchone()
    if not user:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Not found'}, status=404)
    cols = [f'balance_{c}' for c in CURRENCY_LIST]
    vals = {c: user.get(f'balance_{c}', 0) or 0 for c in CURRENCY_LIST}
    cur.execute(
        f"INSERT INTO user_balance_backups (user_id, {', '.join(cols)}) VALUES (?, {', '.join(['?']*len(cols))})",
        [telegram_id] + [vals[c] for c in CURRENCY_LIST]
    )
    for c in CURRENCY_LIST:
        cur.execute(f"UPDATE users SET balance_{c}=0 WHERE user_id=?", (telegram_id,))
    conn.commit()
    log_admin_action(request, f"Обнулил балансы пользователя {telegram_id} (бэкап сохранён)", target_id=telegram_id)
    conn.close()
    return JsonResponse({'success': True, 'message': 'Все балансы обнулены, бэкап сохранён'})


@csrf_exempt
@require_http_methods(["POST"])
@login_required(login_url='/')
def api_restore_balance(request, telegram_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM user_balance_backups WHERE user_id=? AND restored=0 ORDER BY backed_up_at DESC LIMIT 1", (telegram_id,))
    backup = cur.fetchone()
    if not backup:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Нет сохранённого бэкапа'}, status=404)
    for c in CURRENCY_LIST:
        cur.execute(f"UPDATE users SET balance_{c}=COALESCE(balance_{c},0)+? WHERE user_id=?", (backup[f'balance_{c}'] or 0, telegram_id))
    cur.execute("UPDATE user_balance_backups SET restored=1, restored_at=datetime('now') WHERE id=?", (backup['id'],))
    conn.commit()
    log_admin_action(request, f"Восстановил балансы пользователя {telegram_id} из бэкапа #{backup['id']}", target_id=telegram_id)
    conn.close()
    return JsonResponse({'success': True, 'message': 'Балансы восстановлены из бэкапа'})
