import json, os, sys, sqlite3, requests
from datetime import datetime, timedelta
from functools import wraps
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings
from django.utils import timezone
from django.contrib.auth import authenticate, login as auth_login

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..'))
from crypto import encrypt_value, decrypt_value

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, '..', 'novixgift.db')
OWNER_TELEGRAM_ID = int(os.getenv("OWNER_TELEGRAM_ID", "1803437347"))


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
    now = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        conn = get_db()
        cur = conn.cursor()
        desc = f"CEO / Владелец Heyken совершил действие: {action}" if admin_id == OWNER_TELEGRAM_ID else action
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
    admin_name = 'Arkadiex' if admin_id == OWNER_TELEGRAM_ID else request.session.get('username', '')
    ip = _get_client_ip(request)
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


def _get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '0.0.0.0')


# ===================== ROLE-BASED ACCESS CONTROL =====================

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


CURRENCY_LIST = ['RUB','USD','EUR','BYN','UAH','KZT','UZS','TON','USDT','STARS']
CURRENCY_RATES = {'RUB':1,'USD':90,'EUR':95,'BYN':28,'UAH':2.3,'KZT':0.19,'UZS':0.0075,'TON':500,'USDT':90,'STARS':1.5}
CURRENCY_SYMBOLS = {'RUB': '₽', 'USD': '$', 'EUR': '€', 'BYN': 'Br', 'UAH': '₴', 'KZT': '₸', 'UZS': 'so\'m', 'TON': 'TON', 'USDT': 'USDT', 'STARS': '⭐'}
TIER_PRICES = {'premium': 299, 'platinum': 799, 'vip': 1999}
TIER_BADGES = {'free': '⬜ FREE', 'premium': '⭐ PREMIUM', 'platinum': '💎 PLATINUM', 'vip': '👑 VIP'}

FX_RATES = {
    'RUB': 1, 'USD': 95, 'EUR': 100, 'BYN': 29, 'UAH': 2.4,
    'KZT': 0.20, 'UZS': 0.0077, 'TON': 480, 'USDT': 95, 'STARS': 1.5,
}


def _to_rub(amount, currency):
    return amount * FX_RATES.get(currency, 1)


# ===================== AUTH =====================

def login_view(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            username = data.get('username', '').strip()
            password = data.get('password', '')

            django_user = authenticate(request, username=username, password=password) if password else None
            if django_user:
                auth_login(request, django_user)
                uid = int(django_user.username) if django_user.username.lstrip('-').isdigit() else OWNER_TELEGRAM_ID
                request.session['telegram_id'] = uid
                request.session['user_id'] = uid
                request.session['username'] = django_user.first_name or username
                request.session['admin'] = django_user.first_name or username
                request.session['admin_role'] = 'CEO' if django_user.is_superuser else 'Admin'
                if django_user.is_superuser or uid == OWNER_TELEGRAM_ID:
                    request.session['is_owner'] = True
                    request.session['role'] = 'owner'
                request.session.modified = True
                request.session.save()
                return JsonResponse({'success': True, 'token': 'session', 'username': request.session['username'], 'role': 'owner' if django_user.is_superuser else 'admin'})

            if username.lstrip('-').isdigit():
                uid = int(username)
                conn = get_db()
                cur = conn.cursor()
                cur.execute("SELECT * FROM users WHERE user_id=?", (uid,))
                user = cur.fetchone()
                if not user:
                    cur.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (uid, username))
                    conn.commit()
                    cur.execute("SELECT * FROM users WHERE user_id=?", (uid,))
                    user = cur.fetchone()
                conn.close()
                if user:
                    u = dict(user)
                    request.session['telegram_id'] = uid
                    request.session['user_id'] = uid
                    request.session['username'] = u.get('username', str(uid))
                    request.session['admin'] = u.get('username', str(uid))
                    request.session['admin_role'] = u.get('admin_role', 'Admin') or 'Admin'
                    if uid == OWNER_TELEGRAM_ID:
                        request.session['is_owner'] = True
                        request.session['role'] = 'owner'
                    request.session.modified = True
                    request.session.save()
                    return JsonResponse({
                        'success': True, 'token': 'session',
                        'username': u.get('username', str(uid)), 'role': 'owner' if uid == OWNER_TELEGRAM_ID else 'admin'
                    })
            return JsonResponse({'success': False, 'error': 'Пользователь не найден'}, status=401)
        except Exception as e:
            print(f"[login] Error: {e}")
            return JsonResponse({'success': False, 'error': 'Ошибка сервера'}, status=500)
    return render(request, 'login.html')


def logout_view(request):
    request.session.flush()
    return redirect('/')


# ===================== DASHBOARD =====================

@require_permission('dashboard')
def dashboard_view(request):
    log_page_view(request, 'Просмотр Дашборда', 'Администратор перешел на главную панель дашборда')
    ctx = {}; now = datetime.now()
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        total_users = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM users WHERE (premium_tier IS NOT NULL AND premium_tier != 'free') AND (premium_until IS NULL OR premium_until > datetime('now'))")
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
            FROM deals WHERE status='completed' AND commission IS NOT NULL AND commission > 0
        """)
        commission_revenue_raw = cur.fetchone()[0] or 0

        cur.execute("""
            SELECT currency, COALESCE(SUM(amount * commission), 0) AS total
            FROM deals WHERE status='completed' AND commission IS NOT NULL AND commission > 0
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
        for row in cur.fetchall():
            tier = row['premium_tier'] or 'premium'
            days = row['premium_duration_days']
            cnt = row['cnt']
            price_month = TIER_PRICES.get(tier, 299)
            if days >= 99999:
                price_per_unit = price_month * 12
            else:
                price_per_unit = price_month * (days / 30)
            premium_revenue_rub += int(price_per_unit * cnt)

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

        revenue_labels = []; revenue_data = []; users_labels = []; users_data = []
        for i in range(6, -1, -1):
            day = (datetime.now() - timedelta(days=i)).strftime('%d.%m')
            cur.execute("""
                SELECT COALESCE(SUM(amount * commission), 0)
                FROM deals WHERE status='completed' AND commission IS NOT NULL AND commission > 0
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
        pending_disputes = []
        try:
            cur.execute("SELECT * FROM disputes WHERE status='pending' ORDER BY created_at DESC LIMIT 5")
            pending_disputes = [dict(d) for d in cur.fetchall()]
        except Exception:
            pass
        open_tickets = 0
        try:
            cur.execute("SELECT COUNT(*) FROM support_tickets WHERE status='open'")
            open_tickets = cur.fetchone()[0] or 0
        except Exception:
            pass
        conversion = round(premium_users / total_users * 100, 1) if total_users else 0
        conn.close()
        ctx = {
            'active_page': 'dashboard',
            'admin_name': get_admin_name(request),
            'total_users': total_users,
            'active_subs': premium_users,
            'revenue': round(total_revenue_rub, 2),
            'revenue_detail': {'commission': round(commission_revenue_rub, 2), 'premium': round(premium_revenue_rub, 2)},
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
        ctx = {'active_page': 'dashboard', 'admin_name': get_admin_name(request)}
    return render(request, 'dashboard.html', ctx)


# ===================== USERS =====================

@require_permission('users')
def users_view(request):
    log_page_view(request, 'Просмотр Пользователей', 'Администратор открыл список пользователей')
    page = 1; search = ''; user_list = []; total = 0
    try:
        page = int(request.GET.get('page', 1))
        if page < 1: page = 1
    except Exception:
        page = 1
    try:
        search = request.GET.get('q', '').strip()
        per_page = 50
        offset = (page - 1) * per_page
        conn = get_db()
        cur = conn.cursor()
        balance_fields = ', '.join([f'balance_{c}' for c in CURRENCY_LIST])
        columns = f'user_id, username, created_at, premium_tier, admin_role, telegram_username, {balance_fields}'

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
        for u in rows:
            d = dict(u)
            d['telegram_id'] = d.pop('user_id')
            display = d.get('username') or str(d['telegram_id'])
            d['display_name'] = display
            d['avatar_letter'] = display[0].upper()
            d['admin_role'] = d.get('admin_role') or '—'
            total_rub = 0
            for c in CURRENCY_LIST:
                total_rub += (d.get(f'balance_{c}', 0) or 0) * CURRENCY_RATES.get(c, 0)
            d['total_rub'] = round(total_rub, 2)
            tier = d.get('premium_tier', 'free') or 'free'
            d['tier_display'] = TIER_BADGES.get(tier, '⬜ FREE')
            user_list.append(d)
    except Exception as e:
        print(f"[users] Error: {e}")
    return render(request, 'users.html', {
        'active_page': 'users',
        'admin_name': get_admin_name(request),
        'users': user_list,
        'search': search,
        'page': page,
        'total_pages': max(1, (total + 50 - 1) // 50),
        'total': total,
    })


@require_permission('users')
def user_detail_view(request, telegram_id):
    log_page_view(request, 'Просмотр Карточки', f'Администратор открыл карточку пользователя {telegram_id}', target_id=telegram_id)
    user_dict = None; deals = []; disputes = []; referrals = []; inviter = None
    audit_logs = []; latest_backup = None; tickets = []; reviews = []; avg_rating = 0
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=?", (telegram_id,))
        user = cur.fetchone()
        if not user:
            conn.close()
            return render(request, 'user_detail.html', {'active_page':'users', 'admin_name': get_admin_name(request), 'user':None, 'now':datetime.now()})

        for sql, bind in [
            ("SELECT * FROM deals WHERE buyer=? OR seller=? ORDER BY created DESC LIMIT 20", (telegram_id, telegram_id)),
            ("SELECT * FROM disputes WHERE opened_by=? ORDER BY created_at DESC LIMIT 10", (telegram_id,)),
            ("SELECT * FROM users WHERE referred_by=?", (telegram_id,)),
            ("SELECT * FROM users WHERE user_id=(SELECT referred_by FROM users WHERE user_id=?)", (telegram_id,)),
            ("SELECT * FROM audit_logs WHERE description LIKE ? ORDER BY timestamp DESC LIMIT 50", (f'%{telegram_id}%',)),
            ("SELECT * FROM user_balance_backups WHERE user_id=? AND restored=0 ORDER BY backed_up_at DESC LIMIT 1", (telegram_id,)),
            ("SELECT * FROM support_tickets WHERE user_id=? ORDER BY created_at DESC LIMIT 10", (telegram_id,)),
            ("SELECT r.*, d.item AS deal_item FROM reviews r LEFT JOIN deals d ON r.deal_id=d.id WHERE r.reviewed_id=? ORDER BY r.created_at DESC LIMIT 20", (telegram_id,)),
        ]:
            try:
                cur.execute(sql, bind)
                if sql.startswith("SELECT r.*"):
                    reviews = cur.fetchall()
                elif sql.startswith("SELECT * FROM users WHERE referred_by=?"):
                    referrals = cur.fetchall()
                elif sql.startswith("SELECT * FROM users WHERE user_id=("):
                    inviter = cur.fetchone()
                elif sql.startswith("SELECT * FROM audit_logs"):
                    audit_logs = cur.fetchall()
                elif sql.startswith("SELECT * FROM user_balance_backups"):
                    latest_backup = cur.fetchone()
                elif sql.startswith("SELECT * FROM support_tickets"):
                    tickets = cur.fetchall()
                elif sql.startswith("SELECT * FROM disputes"):
                    disputes = cur.fetchall()
                elif sql.startswith("SELECT * FROM deals"):
                    deals = cur.fetchall()
            except Exception as ex:
                print(f"[user_detail subquery] {ex}")

        cur.execute("SELECT AVG(rating) FROM reviews WHERE reviewed_id=?", (telegram_id,))
        avg_rating = cur.fetchone()[0] or 0

        cur.execute(
            "SELECT id, user_id, sender_type, text, timestamp FROM user_messages WHERE user_id=? ORDER BY id ASC",
            (telegram_id,)
        )
        raw_messages = cur.fetchall()
        user_messages = []
        for r in raw_messages:
            d = dict(r)
            d['text'] = decrypt_value(d.get('text', ''))
            user_messages.append(d)

        conn.close()

        user_dict = dict(user)
        user_dict['telegram_id'] = user_dict.pop('user_id')
        display = user_dict.get('username') or str(user_dict['telegram_id'])
        user_dict['display_name'] = display
        user_dict['avatar_letter'] = display[0].upper()

        ref_code = user_dict.get('referral_code') or ''
        bot_username = os.getenv('BOT_USERNAME') or 'NovixBot'
        referral_link = f"https://t.me/{bot_username}?start={ref_code}" if ref_code else ''

        balances = {}
        total_rub = 0
        for c in CURRENCY_LIST:
            val = user_dict.get(f'balance_{c}', 0) or 0
            balances[c] = val
            total_rub += val * CURRENCY_RATES.get(c, 0)
        user_dict['balances'] = balances
        user_dict['total_rub'] = round(total_rub, 2)
        user_dict['referral_link'] = referral_link

        tier = user_dict.get('premium_tier', 'free') or 'free'
        premium_until_raw = user_dict.get('premium_until')
        premium_active = tier != 'free'
        if premium_active and premium_until_raw:
            try:
                pe = datetime.fromisoformat(premium_until_raw.replace('Z',''))
                if pe <= datetime.now():
                    premium_active = False
            except Exception:
                pass

        return render(request, 'user_detail.html', {
            'active_page': 'users',
            'admin_name': get_admin_name(request),
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
            'referral_link': referral_link,
            'user_messages': user_messages,
        })
    except Exception as e:
        print(f"[user_detail] Error: {e}")
    return render(request, 'user_detail.html', {
        'active_page': 'users', 'admin_name': get_admin_name(request),
        'user': user_dict, 'deals': [], 'disputes': [], 'referrals': [],
        'inviter': None, 'invited_users': [],
        'audit_logs': [], 'latest_backup': None,
        'tickets': [], 'reviews': [],
        'avg_rating': 0, 'premium_active': False,
        'now': datetime.now(), 'currencies': CURRENCY_LIST,
        'referral_link': '',
        'user_messages': [],
    })


# ===================== DEALS =====================

@require_permission('deals')
def deals_list_view(request):
    log_page_view(request, 'Просмотр Сделок', 'Администратор открыл список сделок')
    search = ''; status_filter = ''; page = 1; deals = []; total = 0
    try:
        page = int(request.GET.get('page', 1))
        if page < 1: page = 1
    except Exception:
        page = 1
    try:
        search = request.GET.get('q', '').strip()
        status_filter = request.GET.get('status', '').strip()
        per_page = 50
        offset = (page - 1) * per_page
        conn = get_db()
        cur = conn.cursor()
        conditions = []; params = []
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
    except Exception as e:
        print(f"[deals] Error: {e}")
    statuses = ['awaiting', 'completed', 'cancelled', 'disputed']
    return render(request, 'deals_list.html', {
        'active_page': 'deals',
        'admin_name': get_admin_name(request),
        'deals': [dict(d) for d in deals],
        'search': search, 'status_filter': status_filter,
        'statuses': statuses, 'page': page,
        'total_pages': max(1, (total + 50 - 1) // 50),
        'total': total,
    })


# ===================== WITHDRAWALS =====================

@require_permission('withdrawals')
def withdrawals_view(request):
    log_page_view(request, 'Просмотр Выводов', 'Администратор открыл страницу вывода средств')
    ctx = {'active_page': 'withdrawals', 'admin_name': get_admin_name(request), 'requests': [], 'status_filter': '', 'page': 1, 'total_pages': 1, 'total': 0}
    try:
        conn = get_db()
        cur = conn.cursor()
        status_filter = request.GET.get('status', '').strip()
        page = int(request.GET.get('page', 1))
        if page < 1: page = 1
        per_page = 50
        offset = (page - 1) * per_page
        conditions = []; params = []
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
            'admin_name': get_admin_name(request),
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
@require_permission('withdrawals')
def withdrawal_approve_api(request, req_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM withdrawal_requests WHERE id=?", (req_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return JsonResponse({'error': 'Not found'}, status=404)
        req = dict(row)
        cur.execute("UPDATE withdrawal_requests SET status='approved' WHERE id=?", (req_id,))
        cur.execute("UPDATE users SET balance_RUB = balance_RUB - ? WHERE user_id=?", (req['amount'], req['user_id']))
        conn.commit()
        conn.close()
        log_admin_action(request, f"withdrawal_approve", req['user_id'], req['amount'])
        return JsonResponse({'success': True})
    except Exception as e:
        print(f"[withdrawal_approve] Error: {e}")
        return JsonResponse({'error': 'Server error'}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@require_permission('withdrawals')
def withdrawal_reject_api(request, req_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE withdrawal_requests SET status='rejected' WHERE id=?", (req_id,))
        conn.commit()
        conn.close()
        return JsonResponse({'success': True})
    except Exception as e:
        print(f"[withdrawal_reject] Error: {e}")
        return JsonResponse({'error': 'Server error'}, status=500)


# ===================== PROMOCODES =====================

@require_permission('promocodes')
def promocodes_view(request):
    log_page_view(request, 'Просмотр Промокодов', 'Администратор открыл страницу промокодов')
    promo_list = []; now = datetime.now()
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT rowid, * FROM promocodes ORDER BY created_at DESC")
        promos = cur.fetchall()
        conn.close()
        for p in promos:
            d = dict(p)
            d['id'] = d.get('rowid')
            d['discount'] = d.get('amount', 0)
            d['type'] = 'fixed'
            d['used'] = d.get('used_count', 0)
            d['max_uses'] = d.get('max_uses', 0)
            d['expiry_date'] = d.get('expires_at', '')
            d['created_by'] = d.get('created_by', '')
            d['created_at'] = d.get('created_at', '')
            d['code'] = d.get('code', '')
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
    except Exception as e:
        print(f"[promocodes] Error: {e}")
    return render(request, 'promocodes.html', {
        'active_page': 'promocodes',
        'admin_name': get_admin_name(request),
        'promocodes': promo_list,
        'now': now,
    })


# ===================== BROADCAST =====================

@require_permission('broadcast')
def broadcast_view(request):
    log_page_view(request, 'Просмотр Рассылки', 'Администратор открыл страницу рассылок')
    newsletters = []
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM newsletters ORDER BY created_at DESC LIMIT 20")
        newsletters = cur.fetchall()
        conn.close()
    except Exception as e:
        print(f"[broadcast] Error: {e}")
    return render(request, 'broadcast.html', {
        'active_page': 'broadcast',
        'admin_name': get_admin_name(request),
        'newsletters': [dict(n) for n in newsletters],
    })


@csrf_exempt
@require_http_methods(["POST"])
@require_permission('broadcast')
def api_broadcast_send(request):
    try:
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

        sent = 0; failed = 0
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
            except Exception:
                failed += 1

        cur.execute(
            "INSERT INTO newsletters (title, message, sent_count, created_by) VALUES (?, ?, ?, ?)",
            (title, message, sent, request.session.get('telegram_id'))
        )
        conn.commit()
        conn.close()
        log_admin_action(request, f"broadcast_sent to {sent} users", amount=sent)
        return JsonResponse({'success': True, 'sent': sent, 'failed': failed})
    except Exception as e:
        print(f"[broadcast_send] Error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ===================== PROFILE =====================

@session_required
def profile_view(request):
    logs = []
    try:
        log_page_view(request, 'Просмотр Профиля', 'Администратор открыл свой профиль')
        tid = request.session.get('telegram_id')
        is_ceo = tid == OWNER_TELEGRAM_ID
        admin_name = 'Heyken' if is_ceo else get_admin_name(request)
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=?", (tid,))
        user_row = cur.fetchone()
        if user_row:
            u = dict(user_row)
            db_role = u.get('admin_role', 'Admin') or 'Admin'
            db_telegram = u.get('telegram_username', '') or ''
            db_custom_roles = u.get('custom_roles', '[]') or '[]'
        else:
            db_role = 'Admin'
            db_telegram = ''
            db_custom_roles = '[]'
        try:
            parsed_custom_roles = json.loads(db_custom_roles) if isinstance(db_custom_roles, str) else db_custom_roles
        except Exception:
            parsed_custom_roles = []
        display_role = 'CEO' if is_ceo else db_role
        cur.execute("SELECT * FROM audit_logs WHERE user_id=? ORDER BY timestamp DESC LIMIT 50", (tid,))
        logs = cur.fetchall()
        conn.close()
        return render(request, 'profile.html', {
            'active_page': 'profile',
            'admin_name': admin_name,
            'user_name': admin_name,
            'user_username': '@Arkadiex' if is_ceo else (('@' + db_telegram) if db_telegram else ''),
            'user_role': 'CEO / Владелец' if is_ceo else db_role,
            'is_ceo': is_ceo,
            'admin_data': {
                'username': admin_name,
                'role': display_role,
                'custom_roles': parsed_custom_roles,
                'telegram': db_telegram,
                'id': tid,
                'created_at': u.get('created_at', '') if user_row else '',
            },
            'logs': [dict(l) for l in logs],
            'tickets_assigned': 0,
            'closed_tickets': 0,
            'rating': min(100, len(logs)),
            'viewing_self': True,
        })
    except Exception as e:
        print(f"[profile] Error: {e}")
    return render(request, 'profile.html', {
        'active_page': 'profile',
        'admin_name': get_admin_name(request),
        'admin_data': {'username': get_admin_name(request), 'role': 'Администратор', 'custom_roles': []},
        'logs': [], 'tickets_assigned': 0, 'closed_tickets': 0,
        'rating': 0, 'viewing_self': True,
    })


@csrf_exempt
@require_http_methods(["POST"])
@session_required
def api_profile_update(request, username=None):
    try:
        data = json.loads(request.body)
        name = data.get('name', '').strip()
        telegram = data.get('telegram', '').strip().lstrip('@')
        role = data.get('role', '').strip()
        raw_custom_roles = data.get('custom_roles', [])
        tid = request.session.get('telegram_id')
        if not tid:
            return JsonResponse({'success': False, 'error': 'Not authenticated'}, status=401)

        target_id = data.get('target_id')
        if target_id is not None:
            target_id = int(target_id)
            if tid != OWNER_TELEGRAM_ID:
                return JsonResponse({'success': False, 'error': 'Доступ запрещён: только владелец может редактировать других'}, status=403)
            actual_target = target_id
        elif username:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT user_id FROM users WHERE username=? OR user_id=?", (username, username))
            row = cur.fetchone()
            conn.close()
            if not row:
                return JsonResponse({'success': False, 'error': 'Пользователь не найден'}, status=404)
            actual_target = row['user_id']
            if tid != OWNER_TELEGRAM_ID:
                return JsonResponse({'success': False, 'error': 'Доступ запрещён: только владелец может редактировать других'}, status=403)
        else:
            actual_target = tid

        is_self = actual_target == tid
        custom_roles_json = json.dumps(raw_custom_roles, ensure_ascii=False)

        conn = get_db()
        cur = conn.cursor()
        valid_roles = ['Admin', 'Moderator', 'Support', 'Analyst', 'CEO']
        if role and role in valid_roles:
            cur.execute("UPDATE users SET username=?, telegram_username=?, admin_role=?, custom_roles=? WHERE user_id=?",
                        (name or str(actual_target), telegram, role, custom_roles_json, actual_target))
        else:
            cur.execute("UPDATE users SET username=?, telegram_username=?, custom_roles=? WHERE user_id=?",
                        (name or str(actual_target), telegram, custom_roles_json, actual_target))
        conn.commit()

        if is_self:
            request.session['username'] = name or 'Arkadiex'
            request.session['admin'] = name or 'Arkadiex'
            request.session['admin_role'] = role or 'Admin'
            request.session.modified = True
            request.session.save()

        log_admin_action(request, f"Обновил профиль {'себя' if is_self else f'пользователя {actual_target}'} (role={role})", target_id=actual_target)

        conn.close()
        return JsonResponse({'status': 'success', 'success': True, 'message': 'Данные успешно сохранены!'})
    except Exception as e:
        print(f"Ошибка сохранения профиля: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@session_required
def api_profile_change_password(request):
    return JsonResponse({'success': True})


# ===================== DISPUTES / ARBITRATION =====================

@require_permission('disputes')
def disputes_view(request):
    log_page_view(request, 'Просмотр Споров', 'Администратор открыл страницу споров и арбитража')
    disputes_data = []
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT d.*, de.seller, de.buyer, de.amount, de.currency, de.item, de.status AS deal_status
            FROM disputes d
            LEFT JOIN deals de ON d.deal_id = de.id
            ORDER BY d.created_at DESC
        """)
        for d in cur.fetchall():
            row = dict(d)
            dc = row.get('dispute_code') or f"#{row.get('id')}"
            disputes_data.append({
                'id': row.get('id'),
                'dispute_code': dc,
                'username': f"user_{row.get('buyer')}",
                'user_id': row.get('buyer'),
                'user_login': f"User {row.get('buyer')}",
                'subject': f"Спор {dc} по сделке #{row.get('deal_id')} — {row.get('item', 'NFT')}",
                'order_number': f"#{row.get('deal_id')}",
                'user_type': 'buyer' if row.get('initiator') == 'buyer' else 'seller',
                'status': 'open' if row.get('status') == 'pending' else row.get('status', 'closed'),
                'created_at': row.get('created_at', ''),
            })
        conn.close()
    except Exception as e:
        print(f"[disputes] Error: {e}")
    return render(request, 'tickets.html', {
        'active_page': 'tickets',
        'admin_name': get_admin_name(request),
        'tickets': disputes_data,
    })


@csrf_exempt
@session_required
def dispute_detail_api(request, dispute_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT d.*, de.seller, de.buyer, de.amount, de.currency, de.item, de.status AS deal_status,
                   de.created AS deal_created, de.commission
            FROM disputes d
            LEFT JOIN deals de ON d.deal_id = de.id
            WHERE d.id=?
        """, (dispute_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return JsonResponse({'error': 'Not found'}, status=404)
        data = dict(row)
        data['dispute_code'] = data.get('dispute_code') or f"#{data.get('id')}"
        return JsonResponse(data)
    except Exception as e:
        print(f"[dispute_detail] Error: {e}")
        return JsonResponse({'error': 'Server error'}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@require_permission('disputes')
def dispute_resolve_api(request, dispute_id):
    admin_id = request.session.get('telegram_id', 0)
    try:
        data = json.loads(request.body)
        decision = data.get('decision')
        reason = (data.get('reason', '') or '').strip()

        if decision not in ('seller', 'buyer'):
            return JsonResponse({'error': 'Некорректное решение'}, status=400)

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM disputes WHERE id=?", (dispute_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return JsonResponse({'error': 'Спор не найден'}, status=404)
        dispute = dict(row)
        if dispute.get('status') != 'pending':
            conn.close()
            return JsonResponse({'error': 'Спор уже закрыт'}, status=400)

        dispute_code = dispute.get('dispute_code') or f"#{dispute_id}"
        deal_id = dispute['deal_id']
        cur.execute("SELECT * FROM deals WHERE id=?", (deal_id,))
        deal_row = cur.fetchone()
        if not deal_row:
            conn.close()
            return JsonResponse({'error': 'Сделка не найдена'}, status=404)
        deal = dict(deal_row)

        currency = deal['currency']
        amount = float(deal['amount'])
        seller_id = deal['seller']
        buyer_id = deal.get('buyer')

        # Комиссия продавца на основе его Premium-тарифа
        cur.execute("SELECT premium_tier FROM users WHERE user_id=?", (seller_id,))
        tier_row = cur.fetchone()
        tier = tier_row[0] if tier_row else 'free'
        commission_rate = {'free': 0.10, 'premium': 0.05, 'platinum': 0.03, 'vip': 0.0}.get(tier, 0.10)
        commission = amount * commission_rate
        seller_payout = round(amount - commission, 2)

        now = timezone.now().strftime('%Y-%m-%d %H:%M:%S')

        if decision == 'seller':
            cur.execute(f"UPDATE users SET balance_{currency} = COALESCE(balance_{currency},0) + ? WHERE user_id=?",
                        (seller_payout, seller_id))
            cur.execute("UPDATE deals SET status='completed', completed=? WHERE id=?", (now, deal_id))
            cur.execute(
                "UPDATE disputes SET status='seller', resolved_at=?, resolved_by=?, resolution_reason=? WHERE id=?",
                (now, admin_id, reason, dispute_id)
            )
            winner_side = 'продавца'
            action_desc = f"Спор {dispute_code}: победа продавца, выплачено {seller_payout} {currency}, причина: {reason}"
        elif decision == 'buyer':
            if not buyer_id:
                conn.close()
                return JsonResponse({'error': 'Покупатель не указан'}, status=400)
            cur.execute(f"UPDATE users SET balance_{currency} = COALESCE(balance_{currency},0) + ? WHERE user_id=?",
                        (amount, buyer_id))
            cur.execute("UPDATE deals SET status='cancelled', completed=? WHERE id=?", (now, deal_id))
            cur.execute(
                "UPDATE disputes SET status='buyer', resolved_at=?, resolved_by=?, resolution_reason=? WHERE id=?",
                (now, admin_id, reason, dispute_id)
            )
            winner_side = 'покупателя'
            action_desc = f"Спор {dispute_code}: победа покупателя, возвращено {amount} {currency}, причина: {reason}"
        else:
            conn.close()
            return JsonResponse({'error': 'Invalid decision'}, status=400)

        # Аудит с IP
        log_admin_action(request, action_desc, target_id=seller_id)
        conn.commit()
        conn.close()

        # Уведомления через Telegram-бота (через FastAPI эндпоинт bot.py)
        try:
            import requests as http_requests
            bot_token = os.environ.get('BOT_TOKEN', '')
            if bot_token:
                base_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                reason_text = reason or 'Решение администрации.'

                def send_tg(user_id, text):
                    http_requests.post(base_url, json={
                        'chat_id': user_id,
                        'text': text,
                        'parse_mode': 'Markdown'
                    }, timeout=10)

                if decision == 'seller':
                    send_tg(seller_id,
                        f"✅ *Спор {dispute_code} по сделке #{deal_id} успешно закрыт.*\n\n"
                        f"Мнение всех администраторов пало на правоту продавца.\n"
                        f"Вам зачислена сумма: {seller_payout} {currency}\n"
                        f"Официальная причина: {reason_text}")
                    if buyer_id:
                        send_tg(buyer_id,
                            f"⚖️ *Спор {dispute_code} закрыт.*\n\n"
                            f"Решением администрации победа присуждена Продавцу.\n"
                            f"Официальная причина: {reason_text}")
                else:
                    if buyer_id:
                        send_tg(buyer_id,
                            f"↩️ *Спор {dispute_code} по сделке #{deal_id} успешно закрыт.*\n\n"
                            f"Мнение всех администраторов пало на правоту покупателя.\n"
                            f"Вам зачислена сумма: {amount} {currency}\n"
                            f"Официальная причина: {reason_text}")
                    send_tg(seller_id,
                        f"⚖️ *Спор {dispute_code} закрыт.*\n\n"
                        f"Решением администрации победа присуждена Покупателю.\n"
                        f"Официальная причина: {reason_text}")
        except Exception as tg_err:
            print(f"[dispute_resolve] TG notify error: {tg_err}")

        return JsonResponse({
            'success': True,
            'dispute_code': dispute_code,
            'decision': decision,
            'winner_side': winner_side,
            'amount': amount if decision == 'buyer' else seller_payout,
            'currency': currency,
            'reason': reason
        })
    except Exception as e:
        print(f"[dispute_resolve] Error: {e}")
        return JsonResponse({'error': 'Внутренняя ошибка сервера'}, status=500)


# ===================== API: PROMOCODES =====================

@csrf_exempt
@require_http_methods(["POST"])
@require_permission('promocodes')
def api_create_promocode(request):
    try:
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
            "INSERT OR REPLACE INTO promocodes (code, amount, max_uses, expires_at, active, created_by, created_at) VALUES (?, ?, ?, ?, 1, ?, datetime('now'))",
            (code, amount, max_uses, expires_at, request.session.get('telegram_id'))
        )
        conn.commit()
        log_admin_action(request, f"Создал промокод {code} на {amount}")
        conn.close()
        return JsonResponse({'success': True})
    except Exception as e:
        print(f"[create_promocode] Error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@require_permission('promocodes')
def api_update_promocode(request, promo_code):
    try:
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
        cur.execute("UPDATE promocodes SET code=?, amount=?, max_uses=?, expires_at=? WHERE code=?",
                    (code, amount, max_uses, expires_at, promo_code))
        conn.commit()
        log_admin_action(request, f"Обновил промокод {code}")
        conn.close()
        return JsonResponse({'success': True})
    except Exception as e:
        print(f"[update_promocode] Error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["DELETE"])
@require_permission('promocodes')
def api_delete_promocode(request, promo_code):
    try:
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
    except Exception as e:
        print(f"[delete_promocode] Error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
@session_required
def api_get_promocode(request, promo_code):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT rowid, * FROM promocodes WHERE code=?", (promo_code,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return JsonResponse({'error': 'Not found'}, status=404)
        d = dict(row)
        d['id'] = d.get('rowid')
        d['discount'] = d.get('amount', 0)
        d['type'] = 'fixed'
        d['used'] = d.get('used_count', 0)
        d['expiry_date'] = d.get('expires_at', '')
        return JsonResponse(d)
    except Exception as e:
        print(f"[get_promocode] Error: {e}")
        return JsonResponse({'error': 'Server error'}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
@session_required
def api_get_promocodes_list(request):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT rowid, * FROM promocodes ORDER BY created_at DESC")
        result = []
        for p in cur.fetchall():
            d = dict(p)
            d['id'] = d.get('rowid')
            d['discount'] = d.get('amount', 0)
            d['type'] = 'fixed'
            d['used'] = d.get('used_count', 0)
            d['expiry_date'] = d.get('expires_at', '')
            result.append(d)
        conn.close()
        return JsonResponse(result, safe=False)
    except Exception as e:
        print(f"[list_promocodes] Error: {e}")
        return JsonResponse([], safe=False)


# ===================== API: BALANCE =====================

VALID_CURRENCIES = {'RUB','USD','EUR','BYN','UAH','KZT','UZS','TON','USDT','STARS'}

@csrf_exempt
@require_http_methods(["POST"])
@require_permission('users_edit')
def api_change_balance(request, telegram_id):
    try:
        data = json.loads(request.body)
        amount = float(data.get('amount', 0))
        currency = data.get('currency', 'RUB').upper()
        if currency not in VALID_CURRENCIES:
            return JsonResponse({'success': False, 'error': f'Invalid currency: {currency}'}, status=400)
        reason = data.get('reason', '')
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
    except Exception as e:
        print(f"[change_balance] Error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@session_required
def api_send_message(request, telegram_id):
    try:
        data = json.loads(request.body)
        text = data.get('message', '').strip()
        if not text:
            return JsonResponse({'success': False, 'error': 'Пустое сообщение'}, status=400)

        admin_name = get_admin_name(request)
        is_ceo = request.session.get('telegram_id') == OWNER_TELEGRAM_ID
        display_name = 'Владелец - Heyken' if is_ceo else admin_name
        tg_text = f"💬 Сообщение от поддержки NovIX:\n\n{text}\n\n— {display_name}"

        bot_token = os.getenv('BOT_TOKEN') or getattr(settings, 'BOT_TOKEN', '')
        if bot_token:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={'chat_id': telegram_id, 'text': tg_text, 'parse_mode': 'HTML'},
                    timeout=10
                )
            except Exception as e:
                print(f"[send_message] Telegram API error: {e}")

        encrypted_text = encrypt_value(text)
        now = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO user_messages (user_id, sender_type, text, timestamp) VALUES (?, 'admin', ?, ?)",
            (telegram_id, encrypted_text, now)
        )
        conn.commit()
        conn.close()

        log_admin_action(request, f"Отправил сообщение пользователю {telegram_id}: {text[:50]}",
                         target_id=telegram_id)
        return JsonResponse({'success': True, 'message': 'Сообщение отправлено!'})
    except Exception as e:
        print(f"[send_message] Error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
@session_required
def api_get_user_messages(request, telegram_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, user_id, sender_type, text, timestamp FROM user_messages WHERE user_id=? ORDER BY id ASC",
            (telegram_id,)
        )
        rows = cur.fetchall()
        conn.close()
        messages = []
        for r in rows:
            d = dict(r)
            d['text'] = decrypt_value(d.get('text', ''))
            messages.append(d)
        return JsonResponse({'messages': messages, 'count': len(messages)})
    except Exception as e:
        print(f"[get_user_messages] Error: {e}")
        return JsonResponse({'messages': [], 'count': 0})


# ===================== API: PREMIUM =====================

@csrf_exempt
@require_http_methods(["POST"])
@require_permission('users_edit')
def api_grant_premium(request, telegram_id):
    try:
        data = json.loads(request.body)
        tier = data.get('tier', 'free')
        days = int(data.get('days', 30))
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
                    except Exception:
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
    except Exception as e:
        print(f"[grant_premium] Error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ===================== API: ADMIN LOGS / AUDIT =====================

@csrf_exempt
@require_http_methods(["GET"])
@session_required
def api_get_audit_logs(request):
    is_owner = request.session.get('telegram_id') == OWNER_TELEGRAM_ID
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
        print(f"Ошибка API аудита: {e}")
    return JsonResponse({'logs': logs, 'is_owner': is_owner})


@csrf_exempt
@require_http_methods(["POST"])
@require_permission('audit')
def api_clear_audit(request):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM audit_logs")
        conn.commit()
        log_admin_action(request, "Очистил журнал аудита")
        conn.close()
        return JsonResponse({'success': True})
    except Exception as e:
        print(f"[clear_audit] Error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ===================== AJAX SEARCH API =====================

@csrf_exempt
@require_http_methods(["GET"])
@session_required
def api_search_users(request):
    try:
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
            cur.execute(f"SELECT {columns} FROM users ORDER BY created_at DESC LIMIT 50")
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
    except Exception as e:
        print(f"[search_users] Error: {e}")
        return JsonResponse({'users': [], 'total': 0})


@csrf_exempt
@require_http_methods(["GET"])
@session_required
def api_search_deals(request):
    try:
        q = request.GET.get('q', '').strip()
        status_filter = request.GET.get('status', '').strip()
        conn = get_db()
        cur = conn.cursor()
        conditions = []; params = []
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
    except Exception as e:
        print(f"[search_deals] Error: {e}")
        return JsonResponse({'deals': [], 'total': 0})


# ===================== API: OTHER =====================

@csrf_exempt
def api_login(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)
    try:
        data = json.loads(request.body)
        raw = data.get('username', '').strip()
        password = data.get('password', '')

        django_user = authenticate(request, username=raw, password=password) if password else None
        if django_user:
            auth_login(request, django_user)
            uid = int(django_user.username) if django_user.username.lstrip('-').isdigit() else OWNER_TELEGRAM_ID
            request.session['telegram_id'] = uid
            request.session['user_id'] = uid
            request.session['username'] = django_user.first_name or raw
            request.session['admin'] = django_user.first_name or raw
            request.session['admin_role'] = 'CEO' if django_user.is_superuser else 'Admin'
            if django_user.is_superuser:
                request.session['is_owner'] = True
                request.session['role'] = 'owner'
            request.session.modified = True
            request.session.save()
            return JsonResponse({'success': True, 'token': 'session', 'username': request.session['username'], 'role': 'owner' if django_user.is_superuser else 'admin'})

        if raw.lstrip('-').isdigit():
            uid = int(raw)
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE user_id=?", (uid,))
            user = cur.fetchone()
            if not user:
                cur.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (uid, raw))
                conn.commit()
                cur.execute("SELECT * FROM users WHERE user_id=?", (uid,))
                user = cur.fetchone()
            conn.close()
            if user:
                u = dict(user)
                request.session['telegram_id'] = uid
                request.session['user_id'] = uid
                request.session['username'] = u.get('username', str(uid))
                request.session['admin'] = u.get('username', str(uid))
                request.session['admin_role'] = u.get('admin_role', 'Admin') or 'Admin'
                if uid == OWNER_TELEGRAM_ID:
                    request.session['is_owner'] = True
                    request.session['role'] = 'owner'
                request.session.modified = True
                request.session.save()
                return JsonResponse({'success': True, 'token': 'session', 'username': u.get('username', str(uid)), 'role': 'owner' if uid == OWNER_TELEGRAM_ID else 'admin'})
        return JsonResponse({'success': False, 'error': 'Пользователь не найден'}, status=401)
    except Exception as e:
        print(f"[api_login] Error: {e}")
        return JsonResponse({'success': False, 'error': 'Server error'}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
@session_required
def api_export_users(request):
    try:
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
    except Exception as e:
        print(f"[export_users] Error: {e}")
        return JsonResponse({'error': 'Export failed'}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
@session_required
def api_export_audit(request):
    try:
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
    except Exception as e:
        print(f"[export_audit] Error: {e}")
        return JsonResponse({'error': 'Export failed'}, status=500)


# ===================== AUDIT VIEW =====================

@require_permission('audit')
def audit_view(request):
    log_page_view(request, 'Просмотр Логов Аудита', 'Администратор открыл страницу системного аудита')
    tid = request.session.get('telegram_id')
    if tid != OWNER_TELEGRAM_ID and not request.session.get('is_owner'):
        return render(request, 'audit.html', {'active_page': 'audit', 'admin_name': get_admin_name(request), 'logs': []})
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
    return render(request, 'audit.html', {'active_page': 'audit', 'admin_name': get_admin_name(request), 'logs': logs})


# ===================== ADMIN MANAGEMENT (OWNER ONLY) =====================

@require_permission('users')
def admins_view(request):
    log_page_view(request, 'Просмотр Администраторов', 'Администратор открыл страницу управления администраторами')
    tid = request.session.get('telegram_id')
    if tid != OWNER_TELEGRAM_ID and not request.session.get('is_owner'):
        return render(request, 'admins.html', {'active_page': 'admins', 'admin_name': get_admin_name(request), 'admins': []})
    admins_data = []
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT u.user_id, u.username, u.premium_tier FROM users u WHERE u.is_admin=1 OR u.user_id=? ORDER BY u.user_id DESC LIMIT 50",
                    (OWNER_TELEGRAM_ID,))
        rows = cur.fetchall()
        if not rows:
            cur.execute("SELECT u.user_id, u.username, u.premium_tier FROM users u ORDER BY u.user_id DESC LIMIT 50")
            rows = cur.fetchall()
        for row in rows:
            r = dict(row)
            admins_data.append({
                'username': r.get('username') or f"User {r['user_id']}",
                'role': 'CEO' if r['user_id'] == OWNER_TELEGRAM_ID else 'User',
                'telegram': f"User {r['user_id']}",
            })
        conn.close()
    except Exception as e:
        print(f"Ошибка загрузки пользователей: {e}")
    return render(request, 'admins.html', {'active_page': 'admins', 'admin_name': get_admin_name(request), 'admins': admins_data})


@csrf_exempt
@require_http_methods(["POST"])
@session_required
def api_create_admin(request):
    if request.session.get('telegram_id') != OWNER_TELEGRAM_ID and not request.session.get('is_owner'):
        return JsonResponse({'success': False, 'error': 'Forbidden'}, status=403)
    try:
        import uuid
        data = json.loads(request.body)
        username = data.get('username', '').strip()
        telegram_id = data.get('telegram_id', '').strip()
        telegram_username = data.get('telegram_username', '').strip()
        role = data.get('role', 'Admin').strip()
        password = data.get('password', '').strip()

        if not username:
            return JsonResponse({'success': False, 'error': 'Введите имя'}, status=400)

        if role == 'CEO':
            return JsonResponse({'success': False, 'error': 'Роль CEO заблокирована'}, status=403)

        valid_roles = ['Admin', 'Moderator', 'Support']
        if role not in valid_roles:
            role = 'Admin'

        conn = get_db()
        cur = conn.cursor()
        if telegram_id and telegram_id.lstrip('-').isdigit():
            tid = int(telegram_id)
            cur.execute("SELECT user_id FROM users WHERE user_id=?", (tid,))
            if cur.fetchone():
                conn.close()
                return JsonResponse({'success': False, 'error': 'Пользователь с таким Telegram ID уже существует'}, status=400)
            cur.execute("INSERT OR IGNORE INTO users (user_id, username, admin_role) VALUES (?, ?, ?)", (tid, username, role))
            conn.commit()
        else:
            conn.close()
            return JsonResponse({'success': False, 'error': 'Укажите корректный Telegram ID'}, status=400)

        conn.commit()
        conn.close()
        log_admin_action(request, f"Создал администратора {username} (tg: {telegram_username or telegram_id}, role: {role})")
        return JsonResponse({'success': True, 'password': password, 'role': role})
    except Exception as e:
        print(f"[create_admin] Error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@session_required
def api_reset_admin_password(request, username):
    if request.session.get('telegram_id') != OWNER_TELEGRAM_ID and not request.session.get('is_owner'):
        return JsonResponse({'success': False, 'error': 'Forbidden'}, status=403)
    try:
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
    except Exception as e:
        print(f"[reset_admin_password] Error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["DELETE"])
@session_required
def api_delete_admin(request, username):
    if request.session.get('telegram_id') != OWNER_TELEGRAM_ID and not request.session.get('is_owner'):
        return JsonResponse({'success': False, 'error': 'Forbidden'}, status=403)
    try:
        if username.lower() == 'heyken':
            return JsonResponse({'success': False, 'error': 'Нельзя удалить владельца'}, status=400)
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE username=?", (username,))
        conn.commit()
        conn.close()
        log_admin_action(request, f"Удалил администратора {username}")
        return JsonResponse({'success': True})
    except Exception as e:
        print(f"[delete_admin] Error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ===================== ADMIN TICKETS =====================

@require_permission('tickets')
def admin_tickets_view(request):
    log_page_view(request, 'Просмотр Тикетов', 'Администратор открыл список тикетов поддержки')
    tickets_data = []
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM support_tickets ORDER BY updated_at DESC")
        user_cache = {}
        for t in cur.fetchall():
            d = dict(t)
            uid = d.get('user_id')
            if uid not in user_cache:
                cur.execute("SELECT username FROM users WHERE user_id=?", (uid,))
                urow = cur.fetchone()
                user_cache[uid] = urow[0] if urow else str(uid)
            display_name = user_cache[uid]
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
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM support_tickets WHERE id=?", (ticket_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return redirect('/tickets/')
        ticket = dict(row)
        uid = ticket.get('user_id')
        cur.execute("SELECT username FROM users WHERE user_id=?", (uid,))
        urow = cur.fetchone()
        ticket['user_login'] = urow[0] if urow else str(uid)
        cur.execute("SELECT * FROM support_ticket_messages WHERE ticket_id=? ORDER BY created_at", (ticket_id,))
        messages = [dict(m) for m in cur.fetchall()]
        cur.execute("SELECT username FROM admins ORDER BY username")
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
        return JsonResponse({'success': True})
    except Exception as e:
        print(f"[ticket_close] Error: {e}")
        return JsonResponse({'success': False, 'error': 'Server error'}, status=500)


# ===================== BALANCE BACKUP / RESTORE =====================

@csrf_exempt
@require_http_methods(["POST"])
@require_permission('users_edit')
def api_backup_balance(request, telegram_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=?", (telegram_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return JsonResponse({'success': False, 'error': 'Not found'}, status=404)
        u = dict(row)
        cols = [f'balance_{c}' for c in CURRENCY_LIST]
        vals = {c: u.get(f'balance_{c}', 0) or 0 for c in CURRENCY_LIST}
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
    except Exception as e:
        print(f"[backup_balance] Error: {e}")
        return JsonResponse({'success': False, 'error': 'Server error'}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@require_permission('users_edit')
def api_restore_balance(request, telegram_id):
    try:
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
    except Exception as e:
        print(f"[restore_balance] Error: {e}")
        return JsonResponse({'success': False, 'error': 'Server error'}, status=500)


# ===================== REVIEW MODERATION =====================

@csrf_exempt
@require_http_methods(["POST"])
@require_permission('reviews')
def api_moderate_review(request, review_id):
    try:
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
            sets = []; params = []
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
    except Exception as e:
        print(f"[moderate_review] Error: {e}")
        return JsonResponse({'success': False, 'error': 'Server error'}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
@session_required
def api_reported_reviews(request):
    try:
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
    except Exception as e:
        print(f"[reported_reviews] Error: {e}")
        return JsonResponse({'reviews': []})
