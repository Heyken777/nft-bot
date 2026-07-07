import json, os, sys, sqlite3, requests, secrets
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

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:9207")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, '..', 'novixgift.db')
OWNER_TELEGRAM_ID = int(os.getenv("OWNER_TELEGRAM_ID", "1803437347"))


def _get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '0.0.0.0')


def _is_ip_blocked(ip):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM login_attempts "
        "WHERE ip_address = ? AND success = 0 "
        "AND attempted_at > datetime('now', '-15 minutes')",
        (ip,)
    )
    count = cur.fetchone()[0]
    conn.close()
    return count >= 5


def _record_login_attempt(ip, success):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO login_attempts (ip_address, success) VALUES (?, ?)", (ip, 1 if success else 0))
    conn.commit()
    conn.close()


def _initiate_admin_2fa(user_id, username):
    """Создаёт 2FA-код, сохраняет в pending_verifications, отправляет в Telegram.
    Возвращает verify_token или raises Exception."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    verify_token = secrets.token_urlsafe(16)
    payload = json.dumps({"code": code, "attempts": 0})
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO pending_verifications (nonce, user_id, action_type, payload, expires_at) "
            "VALUES (?, ?, ?, ?, datetime('now', '+5 minutes'))",
            (verify_token, user_id, 'admin_login', payload)
        )
        conn.commit()
    finally:
        conn.close()

    resp = requests.post(
        f"{BACKEND_URL}/api/send_admin_login_code",
        json={"user_id": user_id, "code": code},
        timeout=10
    )
    data = resp.json()
    if not data.get('success'):
        raise Exception('Telegram bot not started')
    return verify_token


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=40)
    conn.row_factory = sqlite3.Row
    return conn

def _ensure_profile_reviews_table():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM profile_reviews")
    except sqlite3.OperationalError:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS profile_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reviewer_id INTEGER NOT NULL,
                reviewed_id INTEGER NOT NULL,
                rating INTEGER NOT NULL,
                comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP,
                is_moderated INTEGER DEFAULT 0,
                moderated_by INTEGER,
                moderated_at TIMESTAMP,
                UNIQUE(reviewer_id, reviewed_id)
            )
        """)
        conn.commit()
    conn.close()

LEDGER_PRECISION = {'TON': 4, 'USDT': 4, 'STARS': 0}

def _ledger_round(value, currency):
    p = LEDGER_PRECISION.get(currency, 2)
    return round(value, p)

def _write_ledger(cur, user_id, currency, amount_delta, balance_before, balance_after, operation_type='unknown', reference_id=None, initiated_by=None, note=None):
    cur.execute(
        "INSERT INTO balance_ledger (user_id, currency, amount_delta, balance_before, balance_after, operation_type, reference_id, initiated_by, note) VALUES (?,?,?,?,?,?,?,?,?)",
        (user_id, currency, _ledger_round(amount_delta, currency), _ledger_round(balance_before, currency), _ledger_round(balance_after, currency), operation_type, reference_id, initiated_by, note)
    )


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
    admin_name = request.session.get('username', '') or 'Admin'
    ip = _get_client_ip(request)
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
    audit_logs = []; latest_backup = None; tickets = []; reviews = []; avg_rating = 0; profile_reviews = []
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

        try:
            cur.execute("SELECT pr.*, u.username AS reviewer_name FROM profile_reviews pr LEFT JOIN users u ON pr.reviewer_id=u.user_id WHERE pr.reviewed_id=? ORDER BY pr.created_at DESC LIMIT 20", (telegram_id,))
            profile_reviews = [dict(r) for r in cur.fetchall()]
        except Exception:
            profile_reviews = []

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
        user_dict['avatar_url'] = f'/usersite/avatar/{user_dict["telegram_id"]}/' if user_dict.get('avatar') else None

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
            'profile_reviews': profile_reviews,
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
        'tickets': [], 'reviews': [], 'profile_reviews': [],
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
        rows = cur.fetchall()
        conn.close()
        requests_data = []
        for r in rows:
            d = dict(r)
            if d.get('breakdown'):
                try:
                    d['breakdown'] = json.loads(d['breakdown'])
                except Exception:
                    d['breakdown'] = {}
            else:
                d['breakdown'] = {}
            requests_data.append(d)
        ctx = {
            'active_page': 'withdrawals',
            'admin_name': get_admin_name(request),
            'requests': requests_data,
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
    from decimal import Decimal
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM withdrawal_requests WHERE id=?", (req_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return JsonResponse({'error': 'Not found'}, status=404)
        req = dict(row)

        amount = Decimal(str(req['amount']))
        user_id = req['user_id']

        # Get exchange rates from currency_api (synchronous stale cache)
        from currency_api import currency_api
        raw_rates = currency_api.get_stale_cache("RUB")
        rates = {c: Decimal(str(raw_rates.get(c, 1))) for c in CURRENCY_LIST}

        # Fetch user
        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        user = cur.fetchone()
        if not user:
            conn.close()
            return JsonResponse({'error': 'User not found'}, status=404)
        u = dict(user)

        # Build list of (currency, balance, rub_equivalent) for non-zero balances
        purse = []
        total_rub = Decimal('0')
        for c in CURRENCY_LIST:
            val = Decimal(str(u.get(f'balance_{c}', 0) or 0))
            if val > 0:
                rub_val = val * rates[c]
                purse.append((c, val, rub_val))
                total_rub += rub_val

        if total_rub < amount:
            conn.close()
            log_admin_action(request, "withdrawal_approve_failed_insufficient", user_id, float(amount))
            return JsonResponse({
                'error': f'Недостаточно средств. RUB-эквивалент баланса: {total_rub:.2f}, требуется: {amount:.2f}'
            }, status=400)

        # Deduction order: RUB first, then by descending rub-value (most valuable first)
        purse.sort(key=lambda x: (0 if x[0] == 'RUB' else 1, -x[2]))

        remaining = amount
        deductions = {}
        for c, bal, rub_bal in purse:
            if remaining <= 0:
                break
            rate = rates[c]

            if rub_bal >= remaining:
                # This currency covers the rest
                take_amount = remaining / rate  # full Decimal precision
                if take_amount > bal:
                    take_amount = bal
                new_bal = bal - take_amount
                cur.execute(f"UPDATE users SET balance_{c}=? WHERE user_id=?", (float(new_bal), user_id))
                _write_ledger(cur, user_id, c, -float(take_amount), bal, float(new_bal),
                              'withdrawal', reference_id=str(req_id), initiated_by=request.session.get('telegram_id'), note='Списание при одобрении вывода')
                deducted_rub = take_amount * rate
                deductions[c] = {'amount': float(take_amount), 'rub_value': float(deducted_rub)}
                remaining = Decimal('0')
            else:
                # Take entire balance of this currency
                cur.execute(f"UPDATE users SET balance_{c}=0 WHERE user_id=?", (user_id,))
                _write_ledger(cur, user_id, c, -float(bal), bal, 0,
                              'withdrawal', reference_id=str(req_id), initiated_by=request.session.get('telegram_id'), note='Списание при одобрении вывода')
                deductions[c] = {'amount': float(bal), 'rub_value': float(rub_bal)}
                remaining -= rub_bal

        # Tiny remainder from Decimal division — sweep into last deducted currency
        if remaining > 0 and deductions:
            last_cur = list(deductions.keys())[-1]
            rate = rates[last_cur]
            extra = remaining / rate
            cur.execute(f"SELECT balance_{last_cur} FROM users WHERE user_id=?", (user_id,))
            sweep_before = (cur.fetchone() or [0])[0] or 0
            sweep_after = sweep_before - float(extra)
            cur.execute(
                f"UPDATE users SET balance_{last_cur} = ? WHERE user_id=?",
                (sweep_after, user_id)
            )
            _write_ledger(cur, user_id, last_cur, -float(extra), sweep_before, sweep_after,
                          'withdrawal', reference_id=str(req_id), initiated_by=request.session.get('telegram_id'), note='Остаток списания при одобрении вывода (округление)')
            deductions[last_cur]['amount'] = round(deductions[last_cur]['amount'] + float(extra), 8)
            deductions[last_cur]['rub_value'] = round(deductions[last_cur]['rub_value'] + float(remaining), 8)
            remaining = Decimal('0')

        # Save breakdown + approve
        breakdown_json = json.dumps(deductions, ensure_ascii=False, default=str)
        cur.execute(
            "UPDATE withdrawal_requests SET status='approved', breakdown=? WHERE id=?",
            (breakdown_json, req_id)
        )
        conn.commit()
        conn.close()
        log_admin_action(request, "withdrawal_approve", user_id, float(amount))
        return JsonResponse({'success': True, 'breakdown': deductions})
    except Exception as e:
        print(f"[withdrawal_approve] Error: {e}")
        import traceback; traceback.print_exc()
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
        session_uid = tid
        is_ceo = tid == OWNER_TELEGRAM_ID

        # Support viewing other admins via ?user=username
        target_username = request.GET.get('user', '').strip()
        viewing_self = True
        if target_username and target_username != get_admin_name(request) and is_ceo:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT user_id, username, admin_role, telegram_username, custom_roles, created_at FROM users WHERE username=? AND admin_role IS NOT NULL AND admin_role != ''", (target_username,))
            target_row = cur.fetchone()
            conn.close()
            if target_row:
                tid = target_row['user_id']
                viewing_self = False

        admin_name = get_admin_name(request)
        if not viewing_self:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT username FROM users WHERE user_id=?", (tid,))
            urow = cur.fetchone()
            admin_name = urow[0] if urow else target_username
            conn.close()

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
        display_role = 'CEO' if (tid == OWNER_TELEGRAM_ID) else db_role
        display_name = admin_name

        cur.execute("""
            SELECT COUNT(*) FROM support_tickets
            WHERE assigned_to=? AND status!='closed'
        """, (admin_name,))
        tickets_assigned = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM audit_logs
            WHERE user_id=? AND description LIKE '%Закрыл тикет%'
        """, (tid,))
        closed_tickets = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM audit_logs WHERE user_id=?", (tid,))
        total_actions = cur.fetchone()[0]

        cur.execute("SELECT * FROM audit_logs WHERE user_id=? ORDER BY timestamp DESC LIMIT 50", (tid,))
        raw_logs = cur.fetchall()
        conn.close()
        logs = []
        for r in raw_logs:
            d = dict(r)
            d['action'] = d.get('description', '—')
            logs.append(d)
        return render(request, 'profile.html', {
            'active_page': 'profile',
            'admin_name': admin_name,
            'user_name': admin_name,
            'user_username': f'@{admin_name}' if tid == OWNER_TELEGRAM_ID else (('@' + db_telegram) if db_telegram else ''),
            'user_role': 'CEO / Владелец' if tid == OWNER_TELEGRAM_ID else db_role,
            'is_ceo': session_uid == OWNER_TELEGRAM_ID,
            'admin_data': {
                'username': admin_name,
                'role': display_role,
                'custom_roles': parsed_custom_roles,
                'telegram': db_telegram,
                'id': tid,
                'password': u.get('profile_password_hash', '') if user_row else '',
                'created_at': u.get('created_at', '') if user_row else '',
            },
            'logs': logs,
            'tickets_assigned': tickets_assigned,
            'closed_tickets': closed_tickets,
            'total_actions': total_actions,
            'rating': min(100, total_actions),
            'viewing_self': viewing_self,
        })
    except Exception as e:
        print(f"[profile] Error: {e}")
    return render(request, 'profile.html', {
        'active_page': 'profile',
        'admin_name': get_admin_name(request),
        'admin_data': {'username': get_admin_name(request), 'role': 'Администратор', 'custom_roles': []},
        'logs': [], 'tickets_assigned': 0, 'closed_tickets': 0, 'total_actions': 0,
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
        admin_id = request.session.get('telegram_id')
        conn = get_db()
        cur = conn.cursor()
        cur.execute(f"SELECT balance_{currency} FROM users WHERE user_id=?", (telegram_id,))
        row = cur.fetchone()
        if row is None:
            conn.close()
            return JsonResponse({'success': False, 'error': 'Пользователь не найден'}, status=404)
        balance_before = row[0] or 0
        balance_after = balance_before + amount
        if balance_after < 0:
            conn.close()
            return JsonResponse({'success': False, 'error': 'Недостаточно средств'}, status=400)
        cur.execute(f"UPDATE users SET balance_{currency}=? WHERE user_id=?", (balance_after, telegram_id))
        _write_ledger(cur, telegram_id, currency, amount, balance_before, balance_after,
                      'admin_credit' if amount > 0 else 'admin_debit',
                      initiated_by=admin_id, note=reason or None)
        log_admin_action(request, f"{'Начислил' if amount>0 else 'Списал'} {abs(amount)} {currency} пользователю {telegram_id}: {reason}",
                         target_id=telegram_id, amount=amount)
        conn.commit()
        conn.close()

        try:
            bot_token = os.getenv('BOT_TOKEN') or getattr(settings, 'BOT_TOKEN', '')
            if bot_token:
                sign = '+' if amount > 0 else ''
                tg_text = (
                    f"💳 <b>Изменение баланса</b>\n\n"
                    f"Сумма: {sign}{amount} {currency}\n"
                    f"Причина: {reason or 'Без причины'}"
                )
                requests.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={'chat_id': telegram_id, 'text': tg_text, 'parse_mode': 'HTML'},
                    timeout=10
                )
        except Exception as tg_err:
            print(f"[change_balance] TG notify error: {tg_err}")

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
        display_name = admin_name
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

        try:
            bot_token = os.getenv('BOT_TOKEN') or getattr(settings, 'BOT_TOKEN', '')
            if bot_token:
                if tier == 'free' or days <= 0:
                    tg_text = f"❌ <b>Подписка отключена</b>\n\nВаш тариф сброшен на Free администратором."
                else:
                    tier_label = TIER_BADGES.get(tier, tier)
                    tg_text = (
                        f"🎉 <b>Подписка выдана</b>\n\n"
                        f"Тариф: {tier_label}\n"
                        f"Дней: {days}\n"
                        f"Действует до: {new_expiry[:10] if not isinstance(new_expiry, str) else new_expiry[:10]}"
                    )
                requests.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={'chat_id': telegram_id, 'text': tg_text, 'parse_mode': 'HTML'},
                    timeout=10
                )
        except Exception as tg_err:
            print(f"[grant_premium] TG notify error: {tg_err}")

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
    ip = _get_client_ip(request)
    if _is_ip_blocked(ip):
        return JsonResponse({'success': False, 'error': 'Слишком много попыток. Попробуйте через 15 минут.'}, status=429)
    try:
        data = json.loads(request.body)
        raw = data.get('username', '').strip()
        password = data.get('password', '')
        uid = None

        # — Путь 1: Django superuser —
        django_user = authenticate(request, username=raw, password=password) if password else None
        if django_user:
            uid = int(django_user.username) if django_user.username.lstrip('-').isdigit() else OWNER_TELEGRAM_ID
            username = django_user.first_name or raw
            role = 'CEO' if django_user.is_superuser else 'Admin'

        # — Путь 2: user_id в БД —
        if not django_user and raw.lstrip('-').isdigit():
            uid = int(raw)
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT username, admin_role FROM users WHERE user_id=?", (uid,))
            row = cur.fetchone()
            if not row:
                cur.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (uid, raw))
                conn.commit()
                cur.execute("SELECT username, admin_role FROM users WHERE user_id=?", (uid,))
                row = cur.fetchone()
            conn.close()
            if row:
                username = row['username'] or str(uid)
                role = 'CEO' if uid == OWNER_TELEGRAM_ID else (row['admin_role'] or 'Admin')

        if not uid:
            _record_login_attempt(ip, False)
            return JsonResponse({'success': False, 'error': 'Пользователь не найден'}, status=401)

        # — 2FA: отправляем код в Telegram —
        try:
            verify_token = _initiate_admin_2fa(uid, username)
        except Exception:
            _record_login_attempt(ip, False)
            return JsonResponse({
                'success': False,
                'need_verify': False,
                'error': 'Напишите /start боту, затем попробуйте снова.'
            }, status=400)

        _record_login_attempt(ip, True)
        return JsonResponse({
            'success': True,
            'need_verify': True,
            'verify_token': verify_token,
            'username': username
        })
    except Exception as e:
        print(f"[api_login] Error: {e}")
        return JsonResponse({'success': False, 'error': 'Server error'}, status=500)


@csrf_exempt
def api_verify_login_code(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)
    try:
        data = json.loads(request.body)
        token = data.get('token', '').strip()
        code = data.get('code', '').strip()
        if not token or not code:
            return JsonResponse({'success': False, 'error': 'Missing fields'})
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM pending_verifications WHERE nonce = ? AND action_type = 'admin_login' "
            "AND status = 'pending' AND expires_at > datetime('now')",
            (token,)
        )
        row = cur.fetchone()
        if not row:
            conn.close()
            return JsonResponse({'success': False, 'error': 'Код недействителен или истёк'})
        v = dict(row)
        payload = json.loads(v['payload'])
        attempts = payload.get('attempts', 0)
        if attempts >= 5:
            cur.execute("UPDATE pending_verifications SET status = 'expired' WHERE nonce = ?", (token,))
            conn.commit()
            conn.close()
            return JsonResponse({'success': False, 'error': 'Слишком много попыток. Запросите новый код.'})
        if payload.get('code') != code:
            payload['attempts'] = attempts + 1
            cur.execute("UPDATE pending_verifications SET payload = ? WHERE nonce = ?",
                        (json.dumps(payload), token))
            conn.commit()
            conn.close()
            remaining = 5 - (attempts + 1)
            return JsonResponse({'success': False, 'error': f'Неверный код. Осталось попыток: {remaining}'})

        # — Код верный — выдаём сессию —
        uid = v['user_id']
        cur.execute("SELECT username, admin_role FROM users WHERE user_id=?", (uid,))
        user = cur.fetchone()
        u = dict(user) if user else {}
        username = u.get('username', str(uid))
        admin_role = 'CEO' if uid == OWNER_TELEGRAM_ID else (u.get('admin_role', 'Admin') or 'Admin')

        request.session['telegram_id'] = uid
        request.session['user_id'] = uid
        request.session['username'] = username
        request.session['admin'] = username
        request.session['admin_role'] = admin_role
        if uid == OWNER_TELEGRAM_ID:
            request.session['is_owner'] = True
            request.session['role'] = 'owner'
        request.session.modified = True
        request.session.save()

        cur.execute("UPDATE pending_verifications SET status = 'confirmed' WHERE nonce = ?", (token,))
        conn.commit()
        conn.close()
        return JsonResponse({'success': True, 'token': 'session', 'username': username, 'role': 'owner' if uid == OWNER_TELEGRAM_ID else 'admin'})
    except Exception as e:
        print(f"[api_verify_login_code] Error: {e}")
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
        cur.execute(
            "SELECT u.user_id, u.username, u.admin_role FROM users u "
            "WHERE u.admin_role IS NOT NULL AND u.admin_role != '' "
            "ORDER BY u.user_id DESC LIMIT 50"
        )
        rows = cur.fetchall()
        seen = set()
        for row in rows:
            r = dict(row)
            uid = r['user_id']
            if uid in seen:
                continue
            seen.add(uid)
            role = 'CEO' if uid == OWNER_TELEGRAM_ID else (r.get('admin_role') or 'User')
            display_name = r.get('username') or str(uid)
            admins_data.append({
                'username': display_name,
                'role': role,
                'telegram': display_name,
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
        ticket_role = data.get('ticket_role', 'Администратор').strip()

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
            cur.execute("INSERT OR IGNORE INTO users (user_id, username, admin_role, ticket_role) VALUES (?, ?, ?, ?)", (tid, username, role, ticket_role))
            conn.commit()
        else:
            conn.close()
            return JsonResponse({'success': False, 'error': 'Укажите корректный Telegram ID'}, status=400)

        conn.commit()
        conn.close()
        log_admin_action(request, f"Создал администратора {username} (tg: {telegram_username or telegram_id}, role: {role}, ticket_role: {ticket_role})")
        return JsonResponse({'success': True, 'password': password, 'role': role, 'ticket_role': ticket_role})
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


@csrf_exempt
@require_http_methods(["POST"])
@session_required
@require_permission('reviews')
def api_moderate_profile_review(request, review_id):
    try:
        _ensure_profile_reviews_table()
        conn = get_db()
        cur = conn.cursor()
        data = json.loads(request.body)
        action = data.get('action', '')

        if action == 'delete':
            cur.execute("SELECT reviewed_id FROM profile_reviews WHERE id=?", (review_id,))
            row = cur.fetchone()
            cur.execute("DELETE FROM profile_reviews WHERE id=?", (review_id,))
            if row:
                cur.execute("SELECT AVG(rating), COUNT(*) FROM profile_reviews WHERE reviewed_id=?", (row[0],))
                r2 = cur.fetchone()
                cur.execute("UPDATE users SET rating=?, reviews_count=? WHERE user_id=?", (round(r2[0] or 0, 1), r2[1] or 0, row[0]))
            conn.commit()
            conn.close()
            log_admin_action(request, f"Удалил отзыв на профиль #{review_id}")
            return JsonResponse({'success': True, 'message': 'Отзыв удалён'})

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
            cur.execute(f"UPDATE profile_reviews SET {', '.join(sets)} WHERE id=?", params)
            # Recalculate rating
            cur.execute("SELECT reviewed_id FROM profile_reviews WHERE id=?", (review_id,))
            row = cur.fetchone()
            if row:
                cur.execute("SELECT AVG(rating) FROM profile_reviews WHERE reviewed_id=?", (row[0],))
                avg = cur.fetchone()[0] or 0
                cur.execute("UPDATE users SET rating=? WHERE user_id=?", (round(avg, 1), row[0]))
            conn.commit()
            conn.close()
            log_admin_action(request, f"Изменил отзыв на профиль #{review_id}")
            return JsonResponse({'success': True, 'message': 'Отзыв изменён'})

        conn.close()
        return JsonResponse({'success': False, 'error': 'Неизвестное действие'})
    except Exception as e:
        print(f"[moderate_profile_review] Error: {e}")
        return JsonResponse({'success': False, 'error': 'Server error'}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
@session_required
@require_permission('reviews')
def api_list_profile_reviews(request):
    try:
        _ensure_profile_reviews_table()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT pr.*, u.username AS reviewer_name, u2.username AS reviewed_name
            FROM profile_reviews pr
            LEFT JOIN users u ON pr.reviewer_id = u.user_id
            LEFT JOIN users u2 ON pr.reviewed_id = u2.user_id
            ORDER BY pr.created_at DESC LIMIT 100
        """)
        reviews = [dict(zip([desc[0] for desc in cur.description], row)) for row in cur.fetchall()]
        conn.close()
        return JsonResponse({'reviews': reviews})
    except Exception as e:
        print(f"[list_profile_reviews] Error: {e}")
        return JsonResponse({'reviews': []})


@csrf_exempt
@require_http_methods(["POST"])
@session_required
@require_permission('users')
def api_delete_user_avatar(request, telegram_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT avatar FROM users WHERE user_id=?", (telegram_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return JsonResponse({'success': False, 'error': 'Пользователь не найден'}, status=404)
        old = row[0]
        if old:
            old_path = os.path.join(settings.BASE_DIR, '..', 'media', 'avatars', old)
            if os.path.exists(old_path):
                os.remove(old_path)
        cur.execute("UPDATE users SET avatar='' WHERE user_id=?", (telegram_id,))
        conn.commit()
        conn.close()
        return JsonResponse({'success': True, 'message': 'Аватар удалён'})
    except Exception as e:
        print(f"[delete_avatar] Error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_permission('audit')
def ledger_view(request):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM balance_ledger ORDER BY id DESC LIMIT 200")
        rows = cur.fetchall()
        ledger = [dict(r) for r in rows]
    except Exception as e:
        print(f"[ledger] Error: {e}")
        ledger = []
    finally:
        conn.close()
    return render(request, 'ledger.html', {
        'ledger': ledger,
        'active_page': 'ledger',
        'admin_name': get_admin_name(request),
    })
