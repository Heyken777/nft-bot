import json, os, sqlite3, requests
from datetime import datetime, timedelta
from functools import wraps
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings
from django.contrib.auth import authenticate, login as auth_login

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'novixgift.db')
OWNER_TELEGRAM_ID = 1803437347


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=40)
    conn.row_factory = sqlite3.Row
    return conn


def session_required(view_func):
    @wraps(view_func)
    def _wrapper(request, *args, **kwargs):
        if not request.session.get('telegram_id'):
            return redirect('/')
        return view_func(request, *args, **kwargs)
    return _wrapper


def get_admin_name(request):
    return request.session.get('username') or request.session.get('admin', 'Admin')


def log_admin_action(request, action: str, target_id=None, amount=None):
    admin_id = request.session.get('telegram_id', 0)
    admin_name = 'Arkadiex' if admin_id == OWNER_TELEGRAM_ID else request.session.get('username', '')
    ip = _get_client_ip(request)
    conn = get_db()
    cur = conn.cursor()
    desc = f"CEO / Владелец Heyken совершил действие: {action}" if admin_id == OWNER_TELEGRAM_ID else action
    if target_id:
        desc += f" | target={target_id}"
    if amount:
        desc += f" | amount={amount}"
    cur.execute(
        "INSERT INTO audit_logs (user_id, username, action_type, description, ip_address) VALUES (?, ?, ?, ?, ?)",
        (admin_id, admin_name, action, desc, ip)
    )
    conn.commit()
    conn.close()


# ===================== AUTH =====================

def _get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '0.0.0.0')





def login_view(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        username = data.get('username', '').strip()
        password = data.get('password', '')

        # Try Django auth first (username + password)
        django_user = authenticate(request, username=username, password=password) if password else None
        if django_user:
            auth_login(request, django_user)
            uid = OWNER_TELEGRAM_ID if django_user.is_superuser else int(django_user.username)
            request.session['telegram_id'] = uid
            request.session['user_id'] = uid
            request.session['username'] = username
            request.session['admin'] = username
            if django_user.is_superuser or uid == OWNER_TELEGRAM_ID:
                request.session['is_owner'] = True
                request.session['role'] = 'owner'
            request.session.modified = True
            request.session.save()
            return JsonResponse({'success': True, 'token': 'session', 'username': username, 'role': 'owner' if django_user.is_superuser else 'admin'})

        # Fallback: session-based by telegram_id
        if username.lstrip('-').isdigit():
            uid = int(username)
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE user_id=?", (uid,))
            user = cur.fetchone()
            conn.close()
            if user:
                request.session['telegram_id'] = uid
                request.session['user_id'] = uid
                request.session['username'] = user.get('username', str(uid))
                request.session['admin'] = user.get('username', str(uid))
                if uid == OWNER_TELEGRAM_ID:
                    request.session['is_owner'] = True
                    request.session['role'] = 'owner'
                request.session.modified = True
                request.session.save()
                return JsonResponse({
                    'success': True, 'token': 'session',
                    'username': user.get('username', str(uid)), 'role': 'owner' if uid == OWNER_TELEGRAM_ID else 'admin'
                })
        return JsonResponse({'success': False, 'error': 'Пользователь не найден'}, status=401)
    return render(request, 'login.html')


def logout_view(request):
    request.session.flush()
    return redirect('/')


# ===================== PAGES =====================

PREMIUM_PRICES = {30: 299, 45: 419, 60: 559, 90: 799, 365: 2999}
TIER_BADGES = {'free': '⬜ Free', 'premium': '⭐ Premium', 'platinum': '💎 Platinum', 'vip': '👑 VIP'}
TIER_PRICES = {'premium': 299, 'platinum': 599, 'vip': 1499}
TIER_COMMISSIONS = {'free': 0.04, 'premium': 0.02, 'platinum': 0.01, 'vip': 0.0}

FX_RATES = {'RUB': 1, 'USD': 90, 'EUR': 95, 'BYN': 28, 'UAH': 2.3, 'KZT': 0.19, 'UZS': 0.0075, 'TON': 500, 'USDT': 90, 'STARS': 1.5}

def _to_rub(amount, currency):
    return amount * FX_RATES.get(currency, 1)


@session_required
def dashboard_view(request):
    ctx = {}
    ceo = request.session.get('telegram_id') == OWNER_TELEGRAM_ID
    try:
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

        cur.execute("""
            SELECT COALESCE(SUM(amount * commission), 0)
            FROM deals
            WHERE status='completed' AND commission IS NOT NULL AND commission > 0
        """)
        commission_revenue_raw = cur.fetchone()[0] or 0

        cur.execute("""
            SELECT currency, COALESCE(SUM(amount * commission), 0) AS total
            FROM deals
            WHERE status='completed' AND commission IS NOT NULL AND commission > 0
            GROUP BY currency
        """)
        commission_revenue_rub = 0
        for row in cur.fetchall():
            commission_revenue_rub += _to_rub(row['total'], row['currency'])

        cur.execute("""
            SELECT premium_tier, premium_duration_days, COUNT(*) as cnt
            FROM users
            WHERE premium_tier != 'free' AND premium_duration_days > 0
            GROUP BY premium_tier, premium_duration_days
        """)
        premium_revenue_rub = 0
        tier_breakdown = {}
        for row in cur.fetchall():
            tier = row['premium_tier'] or 'premium'
            days = row['premium_duration_days']
            cnt = row['cnt']
            price_month = TIER_PRICES.get(tier, 299)
            if days >= 99999:
                price_per_unit = price_month * 12
            else:
                price_per_unit = price_month * (days / 30)
            revenue = int(price_per_unit * cnt)
            premium_revenue_rub += revenue
            tier_breakdown[tier] = tier_breakdown.get(tier, 0) + revenue

        cur.execute("""
            SELECT COALESCE(SUM(amount * commission), 0)
            FROM deals
            WHERE status='completed' AND commission IS NOT NULL AND commission > 0
                AND created >= date('now', '-7 days')
        """)
        commission_week_raw = cur.fetchone()[0] or 0

        cur.execute("""
            SELECT currency, COALESCE(SUM(amount * commission), 0) AS total
            FROM deals
            WHERE status='completed' AND commission IS NOT NULL AND commission > 0
                AND created >= date('now', '-7 days')
            GROUP BY currency
        """)
        commission_week_rub = 0
        for row in cur.fetchall():
            commission_week_rub += _to_rub(row['total'], row['currency'])

        total_revenue_rub = commission_revenue_rub + premium_revenue_rub
        revenue_week_rub = commission_week_rub

        cur.execute("SELECT COUNT(*) FROM deals WHERE status='completed'")
        completed_deals = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM deals WHERE status='cancelled'")
        cancelled_deals = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM deals WHERE status='awaiting'")
        awaiting_deals = cur.fetchone()[0] or 0

        revenue_labels = []
        revenue_data = []
        users_labels = []
        users_data = []
        for i in range(6, -1, -1):
            day = (datetime.now() - timedelta(days=i)).strftime('%d.%m')
            cur.execute("""
                SELECT COALESCE(SUM(amount * commission), 0)
                FROM deals
                WHERE status='completed' AND commission IS NOT NULL AND commission > 0
                    AND date(created) = date('now', ?)
            """, (f'-{i} days',))
        rev = cur.fetchone()[0] or 0
        revenue_labels.append(day)
        revenue_data.append(round(rev, 2))
        cur.execute("SELECT COUNT(*) FROM users WHERE date(created_at) = date('now', ?)", (f'-{i} days',))
        usr = cur.fetchone()[0] or 0
        users_labels.append(day)
        users_data.append(usr)

        cur.execute("SELECT user_id, username, balance_RUB, created_at, is_premium FROM users ORDER BY created_at DESC LIMIT 5")
        recent_users = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM deals ORDER BY created DESC LIMIT 5")
        recent_deals = [dict(d) for d in cur.fetchall()]
        try:
            cur.execute("SELECT * FROM disputes WHERE status='pending' ORDER BY created_at DESC LIMIT 5")
            pending_disputes = [dict(d) for d in cur.fetchall()]
        except:
            pending_disputes = []
        try:
            cur.execute("SELECT COUNT(*) FROM support_tickets WHERE status='open'")
            open_tickets = cur.fetchone()[0] or 0
        except:
            open_tickets = 0
        conversion = round(premium_users / total_users * 100, 1) if total_users else 0
        conn.close()
        ctx = {
            'active_page': 'dashboard',
            'admin_name': get_admin_name(request),
            'total_users': total_users,
            'active_subs': premium_users,
            'revenue': round(total_revenue_rub, 2),
            'revenue_detail': {
                'commission': round(commission_revenue_rub, 2),
                'premium': round(premium_revenue_rub, 2),
            },
            'tickets_open': disputes,
            'new_users_week': new_users_week,
            'payments_month': deals_month,
            'active_deals': active_deals,
            'completed_deals': completed_deals,
            'cancelled_deals': cancelled_deals,
            'awaiting_deals': awaiting_deals,
            'revenue_week': round(revenue_week_rub, 2),
            'revenue_labels': json.dumps(revenue_labels),
            'revenue_data': json.dumps(revenue_data),
            'users_labels': json.dumps(users_labels),
            'users_data': json.dumps(users_data),
            'conversion': conversion,
            'recent_users': recent_users,
            'recent_deals': recent_deals,
            'pending_disputes': pending_disputes,
            'open_tickets': open_tickets,
        }
    except Exception as e:
        print(f"[dashboard] Error: {e}")
    return render(request, 'dashboard.html', ctx)


@session_required
def users_view(request):
    conn = get_db()
    cur = conn.cursor()
    search = request.GET.get('q', '').strip()
    page = int(request.GET.get('page', 1))
    per_page = 50
    offset = (page - 1) * per_page

    balance_fields = ', '.join([f'balance_{c}' for c in CURRENCY_LIST])
    columns = f'user_id, username, created_at, premium_tier, {balance_fields}'

    if search:
        cur.execute(f"""
            SELECT {columns}
            FROM users WHERE username LIKE ? OR CAST(user_id AS TEXT) LIKE ?
            ORDER BY created_at DESC LIMIT ? OFFSET ?
        """, (f'%{search}%', f'%{search}%', per_page, offset))
        rows = cur.fetchall()
        cur.execute("SELECT COUNT(*) FROM users WHERE username LIKE ? OR CAST(user_id AS TEXT) LIKE ?",
                    (f'%{search}%', f'%{search}%'))
    else:
        cur.execute(f"""
            SELECT {columns}
            FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?
        """, (per_page, offset))
        rows = cur.fetchall()
        cur.execute("SELECT COUNT(*) FROM users")
    total = cur.fetchone()[0] or 0
    conn.close()
    user_list = []
    for u in rows:
        d = dict(u)
        d['telegram_id'] = d.pop('user_id')
        display = d.get('username') or str(d['telegram_id'])
        d['display_name'] = display
        d['avatar_letter'] = display[0].upper()
        total_rub = 0
        for c in CURRENCY_LIST:
            total_rub += (d.get(f'balance_{c}', 0) or 0) * CURRENCY_RATES.get(c, 0)
        d['total_rub'] = round(total_rub, 2)
        tier = d.get('premium_tier', 'free') or 'free'
        d['tier_display'] = TIER_BADGES.get(tier, '⬜ FREE')
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

@session_required
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
    cur.execute("SELECT * FROM audit_logs WHERE description LIKE ? ORDER BY timestamp DESC LIMIT 50", (f'%{telegram_id}%',))
    audit_logs = cur.fetchall()
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

    tier = user_dict.get('premium_tier', 'free') or 'free'
    premium_until_raw = user_dict.get('premium_until')
    premium_active = tier != 'free'
    if premium_active and premium_until_raw:
        try:
            pe = datetime.fromisoformat(premium_until_raw.replace('Z',''))
            if pe <= datetime.now():
                premium_active = False
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
        'audit_logs': [dict(l) for l in audit_logs],
        'latest_backup': dict(latest_backup) if latest_backup else None,
        'tickets': [dict(t) for t in tickets],
        'reviews': [dict(r) for r in reviews],
        'avg_rating': round(avg_rating, 1),
        'premium_active': premium_active,
        'now': datetime.now(),
        'currencies': CURRENCY_LIST,
    })


@session_required
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

    cur.execute(f"SELECT * FROM deals {where} ORDER BY created DESC LIMIT ? OFFSET ?", params + [per_page, offset])
    deals = cur.fetchall()
    cur.execute(f"SELECT COUNT(*) FROM deals {where}", params)
    total = cur.fetchone()[0] or 0
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


@session_required
def withdrawals_view(request):
    ctx = {'active_page': 'withdrawals', 'requests': [], 'status_filter': '', 'page': 1, 'total_pages': 1, 'total': 0}
    try:
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
        ctx = {
            'active_page': 'withdrawals',
            'requests': [dict(r) for r in requests],
            'status_filter': status_filter,
            'page': page,
            'total_pages': max(1, (total + per_page - 1) // per_page),
            'total': total,
        }
    except Exception as e:
        print(f"[withdrawals] Error: {e}")
    return render(request, 'withdrawals.html', ctx)


@csrf_exempt
@require_http_methods(["POST"])
@session_required
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
@session_required
def withdrawal_reject_api(request, req_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE withdrawal_requests SET status='rejected' WHERE id=?", (req_id,))
    conn.commit()
    conn.close()
    return JsonResponse({'success': True})


@session_required
def promocodes_view(request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT rowid, * FROM promocodes WHERE active=1 ORDER BY created_at DESC")
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


@session_required
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
@session_required
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
        (title, message, sent, request.session.get('telegram_id'))
    )
    conn.commit()
    conn.close()

    log_admin_action(request, f"broadcast_sent to {sent} users", amount=sent)
    return JsonResponse({'success': True, 'sent': sent, 'failed': failed})


@session_required
def profile_view(request):
    is_ceo = request.session.get('telegram_id') == OWNER_TELEGRAM_ID
    admin_name = 'Heyken' if is_ceo else get_admin_name(request)
    role = 'CEO / Владелец' if is_ceo else 'Admin'
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM audit_logs WHERE user_id=? ORDER BY timestamp DESC LIMIT 50",
                (request.session.get('telegram_id'),))
    logs = cur.fetchall()
    conn.close()
    return render(request, 'profile.html', {
        'active_page': 'profile',
        'admin_data': {'username': admin_name, 'role': role, 'custom_roles': []},
        'logs': [dict(l) for l in logs],
        'tickets_assigned': 0,
        'closed_tickets': 0,
        'rating': min(100, len(logs)),
        'viewing_self': True,
    })


# ===================== PROFILE API =====================

@csrf_exempt
@require_http_methods(["POST"])
@session_required
def api_profile_update(request):
    try:
        data = json.loads(request.body)
        name = data.get('name', '').strip()
        tid = request.session.get('telegram_id')
        conn = get_db()
        cur = conn.cursor()
        if tid:
            if tid == OWNER_TELEGRAM_ID:
                cur.execute("UPDATE users SET username=? WHERE user_id=?", (name or 'Arkadiex', tid))
            else:
                cur.execute("UPDATE users SET username=? WHERE user_id=?", (name, tid))
            conn.commit()
        conn.close()
        if name:
            request.session['username'] = name
            request.session['admin'] = name
        return JsonResponse({'status': 'success', 'success': True, 'message': 'Данные успешно сохранены!'})
    except Exception as e:
        print(f"Ошибка сохранения профиля: {e}")
    return JsonResponse({'status': 'success', 'success': True, 'message': 'Данные успешно сохранены!'})


@csrf_exempt
@require_http_methods(["POST"])
@session_required
def api_profile_change_password(request):
    return JsonResponse({'success': True})


# ===================== DISPUTES / ARBITRATION (Stage 5) =====================

@session_required
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
@session_required
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
@session_required
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
@session_required
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
        (code, amount, max_uses, expires_at, request.session.get('telegram_id'))
    )
    conn.commit()
    log_admin_action(request, f"Создал промокод {code} на {amount}")
    conn.close()
    return JsonResponse({'success': True})


@csrf_exempt
@require_http_methods(["POST"])
@session_required
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
@session_required
def api_delete_promocode(request, promo_code):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE promocodes SET active=0, deleted_by=?, deleted_at=datetime('now'), delete_reason='deleted_by_admin' WHERE code=?",
        (request.session.get('telegram_id'), promo_code)
    )
    conn.commit()
    log_admin_action(request, f"Удалил промокод {promo_code}")
    conn.close()
    return JsonResponse({'success': True})


@csrf_exempt
@require_http_methods(["GET"])
@session_required
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
@session_required
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
@session_required
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
@session_required
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
@session_required
def api_grant_premium(request, telegram_id):
    data = json.loads(request.body)
    tier = data.get('tier', 'free')
    days = int(data.get('days', 30))
    admin_name = get_admin_name(request)
    conn = get_db()
    cur = conn.cursor()

    if tier == 'free' or days <= 0:
        cur.execute(
            "UPDATE users SET premium_tier='free', is_premium=0, premium_until=NULL, premium_granted_by=NULL, premium_granted_at=NULL, premium_duration_days=NULL WHERE user_id=?",
            (telegram_id,)
        )
        action = f"Сбросил тариф на Free для пользователя {telegram_id}"
    else:
        if days >= 99999:
            new_expiry = '2099-12-31 23:59:59'
        else:
            cur.execute("SELECT premium_until FROM users WHERE user_id=?", (telegram_id,))
            row = cur.fetchone()
            base = datetime.now()
            if row and row['premium_until']:
                try:
                    base = datetime.fromisoformat(row['premium_until'].replace('Z', ''))
                except:
                    pass
            new_expiry = (base + timedelta(days=days)).isoformat()

        cur.execute(
            "UPDATE users SET premium_tier=?, is_premium=1, premium_until=?, premium_granted_by=?, premium_granted_at=datetime('now'), premium_duration_days=? WHERE user_id=?",
            (tier, new_expiry, request.session.get('telegram_id'), days, telegram_id)
        )
        tier_label = TIER_BADGES.get(tier, tier)
        action = f"Назначил тариф {tier_label} на {days} дн. пользователю {telegram_id}"

    conn.commit()
    log_admin_action(request, action, target_id=telegram_id)
    conn.close()
    return JsonResponse({'success': True})


# ===================== API: ADMIN LOGS / AUDIT =====================

@csrf_exempt
@require_http_methods(["GET"])
@session_required
def api_get_audit_logs(request):
    is_owner = request.session.get('telegram_id') == OWNER_TELEGRAM_ID
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT 100")
        rows = cur.fetchall()
        logs = []
        for r in rows:
            d = dict(r)
            if d.get('user_id'):
                cur.execute("SELECT username FROM users WHERE user_id=?", (d['user_id'],))
                u = cur.fetchone()
                d['admin_name'] = u['username'] if u else str(d['user_id'])
            else:
                d['admin_name'] = '—'
            d['ip_address'] = d.pop('ip_address', '—') or '—'
            d['action'] = d.get('action_type', '—')
            logs.append(d)
        conn.close()
    except Exception as e:
        print(f"Ошибка API аудита: {e}")
        logs = []
    return JsonResponse({'logs': logs, 'is_owner': is_owner})


@csrf_exempt
@require_http_methods(["POST"])
@session_required
def api_clear_audit(request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM audit_logs")
    conn.commit()
    log_admin_action(request, "Очистил журнал аудита")
    conn.close()
    return JsonResponse({'success': True})


# ===================== AJAX SEARCH API =====================

@csrf_exempt
@require_http_methods(["GET"])
@session_required
def api_search_users(request):
    q = request.GET.get('q', '').strip()
    conn = get_db()
    cur = conn.cursor()
    balance_fields = ', '.join([f'balance_{c}' for c in CURRENCY_LIST])
    columns = f'user_id, username, created_at, premium_tier, {balance_fields}'
    if q:
        cur.execute(f"""
            SELECT {columns}
            FROM users WHERE username LIKE ? OR CAST(user_id AS TEXT) LIKE ?
            ORDER BY created_at DESC LIMIT 50
        """, (f'%{q}%', f'%{q}%'))
    else:
        cur.execute(f"""
            SELECT {columns}
            FROM users ORDER BY created_at DESC LIMIT 50
        """)
    rows = cur.fetchall()
    conn.close()
    result = []
    for u in rows:
        d = dict(u)
        d['telegram_id'] = d.pop('user_id')
        d['avatar_letter'] = (d.get('username') or str(d['telegram_id']))[0].upper()
        total_rub = 0
        for c in CURRENCY_LIST:
            total_rub += (d.get(f'balance_{c}', 0) or 0) * CURRENCY_RATES.get(c, 0)
        d['total_rub'] = round(total_rub, 2)
        tier = d.get('premium_tier', 'free') or 'free'
        d['tier_display'] = TIER_BADGES.get(tier, '⬜ FREE')
        result.append(d)
    return JsonResponse({'users': result, 'total': len(result)})


@csrf_exempt
@require_http_methods(["GET"])
@session_required
def api_search_deals(request):
    q = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '').strip()
    conn = get_db()
    cur = conn.cursor()
    conditions = []
    params = []
    if q:
        conditions.append("(CAST(id AS TEXT) LIKE ? OR item LIKE ?)")
        params.extend([f'%{q}%', f'%{q}%'])
    if status_filter:
        conditions.append("status=?")
        params.append(status_filter)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    cur.execute(f"SELECT * FROM deals {where} ORDER BY created DESC LIMIT 50", params)
    rows = cur.fetchall()
    conn.close()
    return JsonResponse({'deals': [dict(d) for d in rows], 'total': len(rows)})


# ===================== API: OTHER =====================

@csrf_exempt
def api_login(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)
    data = json.loads(request.body)
    raw = data.get('username', '').strip()
    password = data.get('password', '')

    django_user = authenticate(request, username=raw, password=password) if password else None
    if django_user:
        auth_login(request, django_user)
        uid = OWNER_TELEGRAM_ID if django_user.is_superuser else int(django_user.username)
        request.session['telegram_id'] = uid
        request.session['user_id'] = uid
        request.session['username'] = raw
        request.session['admin'] = raw
        if django_user.is_superuser:
            request.session['is_owner'] = True
            request.session['role'] = 'owner'
        request.session.modified = True
        request.session.save()
        return JsonResponse({'success': True, 'token': 'session', 'username': raw, 'role': 'owner' if django_user.is_superuser else 'admin'})

    if raw.lstrip('-').isdigit():
        uid = int(raw)
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=?", (uid,))
        user = cur.fetchone()
        conn.close()
        if user:
            request.session['telegram_id'] = uid
            request.session['user_id'] = uid
            request.session['username'] = user.get('username', str(uid))
            request.session['admin'] = user.get('username', str(uid))
            if uid == OWNER_TELEGRAM_ID:
                request.session['is_owner'] = True
                request.session['role'] = 'owner'
            request.session.modified = True
            request.session.save()
            return JsonResponse({'success': True, 'token': 'session', 'username': user.get('username', str(uid)), 'role': 'owner' if uid == OWNER_TELEGRAM_ID else 'admin'})
    return JsonResponse({'success': False, 'error': 'Пользователь не найден'}, status=401)


@csrf_exempt
@require_http_methods(["GET"])
@session_required
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
@session_required
def api_export_audit(request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM audit_logs ORDER BY timestamp DESC")
    logs = cur.fetchall()
    conn.close()
    import csv
    from django.http import HttpResponse
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="audit_logs.csv"'
    writer = csv.writer(response)
    writer.writerow(['Время', 'User ID', 'Username', 'Тип', 'Описание', 'IP'])
    for l in logs:
        writer.writerow([l['timestamp'], l['user_id'], l['username'], l['action_type'], l['description'], l['ip_address']])
    return response


# ===================== AUDIT VIEW =====================

@session_required
def audit_view(request):
    tid = request.session.get('telegram_id')
    if tid != OWNER_TELEGRAM_ID and not request.session.get('is_owner'):
        from django.http import HttpResponse; return HttpResponse('Ошибка. Вернитесь на главную: http://127.0.0.1:8000/usersite/', status=403)
    logs = []
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT 100")
        for r in cur.fetchall():
            d = dict(r)
            if d.get('user_id'):
                cur.execute("SELECT username FROM users WHERE user_id=?", (d['user_id'],))
                u = cur.fetchone()
                d['admin_name'] = u['username'] if u else str(d['user_id'])
            else:
                d['admin_name'] = '—'
            d['ip_address'] = d.pop('ip_address', '—') or '—'
            d['action'] = d.get('action_type', '—')
            logs.append(d)
        conn.close()
    except Exception as e:
        print(f"Ошибка аудита: {e}")
        logs = []
    return render(request, 'audit.html', {'active_page': 'audit', 'logs': logs})


# ===================== ADMIN MANAGEMENT (OWNER ONLY) =====================

@session_required
def admins_view(request):
    tid = request.session.get('telegram_id')
    if tid != OWNER_TELEGRAM_ID and not request.session.get('is_owner'):
        from django.http import HttpResponse; return HttpResponse('Ошибка. Вернитесь на главную: http://127.0.0.1:8000/usersite/', status=403)
    admins_data = []
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT DISTINCT u.user_id, u.username, u.premium_tier FROM users u ORDER BY u.user_id DESC LIMIT 50")
        for row in cur.fetchall():
            admins_data.append({
                'username': row['username'] or f"User {row['user_id']}",
                'role': 'CEO' if row['user_id'] == OWNER_TELEGRAM_ID else 'User',
                'telegram': f"User {row['user_id']}",
            })
    except Exception as e:
        print(f"Ошибка загрузки пользователей: {e}")
    conn.close()
    return render(request, 'admins.html', {'active_page': 'admins', 'admins': admins_data})


@csrf_exempt
@require_http_methods(["POST"])
@session_required
def api_create_admin(request):
    if request.session.get('telegram_id') != OWNER_TELEGRAM_ID and not request.session.get('is_owner'):
        return JsonResponse({'success': False, 'error': 'Forbidden'}, status=403)
    import uuid
    data = json.loads(request.body)
    username = data.get('username', '').strip()
    telegram_id = data.get('telegram_id', '').strip()
    telegram_username = data.get('telegram_username', '').strip()
    if not username:
        return JsonResponse({'success': False, 'error': 'Введите имя'}, status=400)
    conn = get_db()
    cur = conn.cursor()
    if telegram_id and telegram_id.lstrip('-').isdigit():
        cur.execute("SELECT user_id FROM users WHERE user_id=?", (int(telegram_id),))
        if cur.fetchone():
            conn.close()
            return JsonResponse({'success': False, 'error': 'Пользователь уже существует'}, status=400)
        cur.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (int(telegram_id), username))
        conn.commit()
        password = None
        try:
            cur.execute("UPDATE users SET username=? WHERE user_id=?", (f"{username} (admin)", int(telegram_id)))
        except Exception:
            pass
    conn.commit()
    conn.close()
    log_admin_action(request, f"Создал администратора {username} (tg: {telegram_username or telegram_id})")
    return JsonResponse({'success': True, 'password': password})


@csrf_exempt
@require_http_methods(["POST"])
@session_required
def api_reset_admin_password(request, username):
    if request.session.get('telegram_id') != OWNER_TELEGRAM_ID and not request.session.get('is_owner'):
        return JsonResponse({'success': False, 'error': 'Forbidden'}, status=403)
    import uuid
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id, username FROM users WHERE username=? LIMIT 1", (username,))
    u = cur.fetchone()
    if not u:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Не найден'}, status=404)
    conn.close()
    new_password = str(uuid.uuid4())[:12]
    log_admin_action(request, f"Сбросил пароль администратора {username}")
    return JsonResponse({'success': True, 'new_password': new_password})


@csrf_exempt
@require_http_methods(["DELETE"])
@session_required
def api_delete_admin(request, username):
    if request.session.get('telegram_id') != OWNER_TELEGRAM_ID and not request.session.get('is_owner'):
        return JsonResponse({'success': False, 'error': 'Forbidden'}, status=403)
    if username.lower() == 'heyken':
        return JsonResponse({'success': False, 'error': 'Нельзя удалить владельца'}, status=400)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE username=?", (username,))
    conn.commit()
    conn.close()
    log_admin_action(request, f"Удалил администратора {username}")
    return JsonResponse({'success': True})


# ===================== ADMIN TICKETS =====================

@session_required
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


@session_required
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
@session_required
def admin_ticket_reply_api(request, ticket_id):
    data = json.loads(request.body)
    message = data.get('message', '').strip()
    if not message:
        return JsonResponse({'success': False, 'error': 'Пустое сообщение'}, status=400)
    is_ceo = request.session.get('telegram_id') == OWNER_TELEGRAM_ID
    admin_name = 'Владелец - Heyken' if is_ceo else get_admin_name(request)
    display_message = f"💬 Сообщение от Владельца - Heyken: {message}" if is_ceo else message
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO support_ticket_messages (ticket_id, sender_type, sender_name, message) VALUES (?,'admin',?,?)",
        (ticket_id, admin_name, display_message)
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
@session_required
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
@session_required
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
@session_required
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
@session_required
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
@session_required
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


# ===================== REVIEW MODERATION =====================

@csrf_exempt
@require_http_methods(["POST"])
@session_required
def api_moderate_review(request, review_id):
    conn = get_db()
    cur = conn.cursor()
    data = json.loads(request.body)
    action = data.get('action', '')

    if action == 'delete':
        cur.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
        conn.commit()
        conn.close()
        log_admin_action(request, f"Удалил отзыв #{review_id}", target_id=0)
        return JsonResponse({'success': True, 'message': 'Отзыв удалён'})

    if action == 'moderate':
        is_moderated = data.get('is_moderated', 1)
        cur.execute("UPDATE reviews SET is_moderated = ?, moderated_at = datetime('now') WHERE id = ?",
                    (is_moderated, review_id))
        conn.commit()
        conn.close()
        log_admin_action(request, f"Промодерировал отзыв #{review_id}", target_id=0)
        return JsonResponse({'success': True, 'message': 'Статус модерации обновлён'})

    if action == 'edit':
        rating = data.get('rating')
        comment = data.get('comment')
        sets = []
        params = []
        if rating is not None:
            sets.append("rating = ?")
            params.append(int(rating))
        if comment is not None:
            sets.append("comment = ?")
            params.append(comment)
        if not sets:
            conn.close()
            return JsonResponse({'success': False, 'error': 'Нет данных для изменения'})
        params.append(review_id)
        cur.execute(f"UPDATE reviews SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
        conn.close()
        log_admin_action(request, f"Изменил отзыв #{review_id}", target_id=0)
        return JsonResponse({'success': True, 'message': 'Отзыв изменён'})

    conn.close()
    return JsonResponse({'success': False, 'error': 'Неизвестное действие'})



@csrf_exempt
@require_http_methods(["GET"])
@session_required
def api_reported_reviews(request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT r.*, d.item AS deal_item
        FROM reviews r
        LEFT JOIN deals d ON r.deal_id = d.id
        WHERE r.reported = 1
        ORDER BY r.created_at DESC
    """)
    reviews = [dict(zip([desc[0] for desc in cur.description], row)) for row in cur.fetchall()]
    conn.close()
    return JsonResponse({'reviews': reviews})
