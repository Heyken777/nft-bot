import json, os, sqlite3, hashlib, hmac, random, re, time, requests
from datetime import datetime, timedelta
from decimal import Decimal
from functools import wraps
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django_ratelimit.decorators import ratelimit

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, '..', 'novixgift.db')

import sys
sys.path.insert(0, os.path.join(BASE_DIR, '..'))
from users.crypto_utils import decrypt_value, is_encryption_enabled as _enc_enabled
from users.views import OWNER_TELEGRAM_ID
from id_generator import generate_deal_code
from fee_calculator import get_user_fee_rate as _get_fee_rate, get_user_volume_tier_info as _get_vol_tier_info


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=40)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=40000;")
    conn.row_factory = sqlite3.Row
    return conn


def _get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '0.0.0.0')


def log_user_action(user_id, action_type, details='', request=None):
    ip_address = _get_client_ip(request) if request else '0.0.0.0'
    user_agent = request.META.get('HTTP_USER_AGENT', '') if request else ''
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO user_audit_log (user_id, action_type, details, ip_address, user_agent) VALUES (?, ?, ?, ?, ?)",
        (user_id, action_type, details, ip_address, user_agent)
    )
    conn.commit()
    conn.close()


def check_new_device_and_notify(user_id, request):
    _ensure_known_devices_table()
    ip = _get_client_ip(request)
    ua = request.META.get('HTTP_USER_AGENT', '')
    session_key = request.session.session_key or ''
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM known_devices WHERE user_id=? AND ip_address=? AND user_agent=?",
        (user_id, ip, ua)
    )
    known = cur.fetchone()
    if known:
        cur.execute(
            "UPDATE known_devices SET last_seen=CURRENT_TIMESTAMP, session_key=? WHERE user_id=? AND ip_address=? AND user_agent=?",
            (session_key, user_id, ip, ua)
        )
        conn.commit()
        conn.close()
        return
    cur.execute(
        "INSERT INTO known_devices (user_id, ip_address, user_agent, session_key) VALUES (?, ?, ?, ?)",
        (user_id, ip, ua, session_key)
    )
    conn.commit()
    conn.close()
    try:
        requests.post(
            f"{BACKEND_URL}/api/internal/notify-new-login",
            json={'user_id': user_id, 'ip_address': ip, 'user_agent': ua},
            timeout=5
        )
    except Exception:
        pass


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




def safe_db(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except sqlite3.Error as e:
            if len(args) > 0 and hasattr(args[0], 'META'):
                from django.http import HttpResponse
                return HttpResponse('Ошибка. Вернитесь на главную: http://127.0.0.1:8000/usersite/', status=500)
            return JsonResponse({'success': False, 'error': 'Database error'}, status=500)
        except Exception as e:
            if len(args) > 0 and hasattr(args[0], 'META'):
                from django.http import HttpResponse
                return HttpResponse('Ошибка. Вернитесь на главную: http://127.0.0.1:8000/usersite/', status=500)
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    return wrapper


def check_auth(request):
    uid = request.session.get('user_id')
    if uid is not None:
        return True
    tid = request.session.get('telegram_id')
    if tid:
        request.session['user_id'] = tid
        if 'admin_role' not in request.session:
            try:
                conn = get_db()
                cur = conn.cursor()
                cur.execute("SELECT admin_role FROM users WHERE user_id=?", (tid,))
                row = cur.fetchone()
                conn.close()
                if row and row[0]:
                    request.session['admin_role'] = row[0]
            except Exception:
                pass
        return True
    return False


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
    u = dict(user)
    u['admin_role'] = u.get('admin_role') or None
    return u


def landing_view(request):
    bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', 'NovixGiftBot')
    top_sellers = []
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT u.user_id, u.username,
                   COALESCE(AVG(r.rating), 0) as avg_rating,
                   (SELECT COUNT(*) FROM deals WHERE seller = u.user_id AND status = 'completed') as completed_deals
            FROM users u
            LEFT JOIN reviews r ON r.reviewed_id = u.user_id
            WHERE (SELECT COUNT(*) FROM deals WHERE seller = u.user_id AND status = 'completed') > 0
            GROUP BY u.user_id
            ORDER BY avg_rating DESC, completed_deals DESC
            LIMIT 10
        """)
        top_sellers = [dict(zip([desc[0] for desc in cur.description], row)) for row in cur.fetchall()]
        conn.close()
    except Exception as e:
        print(f"Ошибка landing top_sellers: {e}")
    return render(request, 'usersite/landing.html', {
        'bot_username': bot_username,
        'bot_link': f"https://t.me/{bot_username}",
        'top_sellers': top_sellers,
    })

def user_login_view(request):
    bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', 'NovixGiftBot')
    return render(request, 'usersite/login.html', {
        'bot_username': bot_username,
        'bot_link': f"https://t.me/{bot_username}",
    })


@ratelimit(key='ip', rate='10/m', block=True)
def telegram_auth_view(request):
    token = request.GET.get('token')
    code = request.GET.get('code')

    if token:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=?", (OWNER_TELEGRAM_ID,))
        user = cur.fetchone()
        conn.close()
        if user:
            user_id = user['user_id']
            request.session['user_id'] = user_id
            request.session['telegram_id'] = user_id
            request.session['username'] = user['username'] if user['username'] else str(user_id)
            request.session['admin_role'] = user.get('admin_role') or None
            request.session['is_owner'] = True
            request.session['role'] = 'owner'
            request.session.modified = True
            request.session.save()
            check_new_device_and_notify(user_id, request)
            return redirect('/usersite/profile/')

    if code and code.isdigit() and len(code) == 6:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM auth_codes WHERE code=? AND expires_at > datetime('now')", (code,))
        auth = cur.fetchone()
        if auth:
            uid = auth['user_id']
            user = get_or_create_user(uid)
            request.session['user_id'] = uid
            request.session['telegram_id'] = uid
            request.session['username'] = user.get('username', str(uid))
            request.session['admin_role'] = user.get('admin_role') or None
            request.session.set_expiry(86400 * 7)
            if uid == OWNER_TELEGRAM_ID:
                request.session['is_owner'] = True
                request.session['role'] = 'owner'
            request.session.modified = True
            request.session.save()
            cur.execute("DELETE FROM auth_codes WHERE code=?", (code,))
            conn.commit()
            conn.close()
            check_new_device_and_notify(uid, request)
            # Если профиль ещё не заполнен → редирект на страницу регистрации
            profile_complete = user.get('profile_setup_complete', 0)
            if not profile_complete:
                return redirect('/usersite/register/')
            return redirect('/usersite/profile/')
        conn.close()

    return redirect('/usersite/login/')


def register_profile_view(request):
    """Страница создания логина/пароля/email после Telegram-авторизации."""
    uid = request.session.get('user_id')
    username = request.session.get('username', '')
    if not uid:
        return redirect('/usersite/login/')
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT profile_setup_complete FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    conn.close()
    if row and row[0]:
        return redirect('/usersite/profile/')
    return render(request, 'usersite/register_profile.html', {
        'telegram_id': uid,
        'username': username,
    })


@csrf_exempt
@require_http_methods(["POST"])
def save_profile_api(request):
    """Сохранение логина, email и пароля после Telegram-авторизации."""
    uid = request.session.get('user_id')
    if not uid:
        return JsonResponse({'success': False, 'error': 'Не авторизован'}, status=401)
    data = json.loads(request.body)
    login = data.get('login', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '')
    if not login:
        return JsonResponse({'success': False, 'error': 'Введите логин'}, status=400)
    if not re.match(r'^[a-zA-Z0-9_]+$', login):
        return JsonResponse({'success': False, 'error': 'Логин: только латиница, цифры и _'}, status=400)
    if len(login) < 3 or len(login) > 32:
        return JsonResponse({'success': False, 'error': 'Логин от 3 до 32 символов'}, status=400)
    if len(password) < 6:
        return JsonResponse({'success': False, 'error': 'Пароль минимум 6 символов'}, status=400)
    if email and not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
        return JsonResponse({'success': False, 'error': 'Некорректный email'}, status=400)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE profile_login=? AND user_id!=?", (login, uid))
    if cur.fetchone():
        conn.close()
        return JsonResponse({'success': False, 'error': 'Этот логин уже занят'}, status=400)
    password_hash = make_password(password)
    cur.execute(
        "UPDATE users SET profile_login=?, profile_password_hash=?, profile_email=?, profile_setup_complete=1 WHERE user_id=?",
        (login, password_hash, email or None, uid)
    )
    conn.commit()
    conn.close()
    log_user_action(uid, 'password_changed', 'Установлен пароль при регистрации', request)
    if email:
        log_user_action(uid, 'profile_updated', f'Указан email: {email}', request)
    return JsonResponse({'success': True})


@csrf_exempt
@require_http_methods(["POST"])
def local_login_api(request):
    """Вход по логину/email и паролю."""
    data = json.loads(request.body)
    login = data.get('login', '').strip().lower()
    password = data.get('password', '')
    if not login or not password:
        return JsonResponse({'success': False, 'error': 'Заполните все поля'}, status=400)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, profile_login, profile_password_hash, username FROM users "
        "WHERE (LOWER(profile_login)=? OR LOWER(profile_email)=?) AND profile_setup_complete=1",
        (login, login)
    )
    user = cur.fetchone()
    conn.close()
    if not user:
        return JsonResponse({'success': False, 'error': 'Пользователь не найден'}, status=404)
    user_id, profile_login, password_hash, username = user
    if not password_hash or not check_password(password, password_hash):
        return JsonResponse({'success': False, 'error': 'Неверный пароль'}, status=401)
    request.session['user_id'] = user_id
    request.session['telegram_id'] = user_id
    request.session['username'] = username or str(user_id)
    conn2 = get_db()
    cur2 = conn2.cursor()
    cur2.execute("SELECT admin_role FROM users WHERE user_id=?", (user_id,))
    row = cur2.fetchone()
    request.session['admin_role'] = row[0] if row and row[0] else None
    conn2.close()
    request.session.set_expiry(86400 * 7)
    request.session.modified = True
    request.session.save()
    check_new_device_and_notify(user_id, request)
    return JsonResponse({'success': True})


@csrf_exempt
@require_http_methods(["POST"])
def notifications_mark_read(request):
    if not check_auth(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    user_id = request.session.get('user_id')
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    return JsonResponse({'success': True})


@csrf_exempt
@require_http_methods(["POST"])
def request_code_api(request):
    data = json.loads(request.body)
    raw = data.get('telegram_id', '').strip()
    if not raw:
        return JsonResponse({'success': False, 'error': 'Введите Telegram ID или @username'}, status=400)

    conn = get_db()
    cur = conn.cursor()

    if raw.lstrip('-').isdigit():
        user_id = int(raw)
        cur.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    else:
        username = raw.lstrip('@').strip()
        cur.execute("SELECT user_id FROM users WHERE username=?", (username,))

    row = cur.fetchone()
    if not row:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Пользователь не найден'}, status=404)

    user_id = row['user_id']
    code = f"{random.randint(100000, 999999)}"
    cur.execute(
        "INSERT INTO auth_codes (user_id, code, expires_at) VALUES (?, ?, datetime('now', '+5 minutes'))",
        (user_id, code)
    )
    cur.execute(
        "INSERT INTO notifications (user_id, title, message) VALUES (?, 'Код авторизации', ?)",
        (user_id, f"🔐 Ваш код для входа на сайт: {code}\nДействителен 5 минут.\n\nВведите его на странице входа или отправьте боту команду /code")
    )
    conn.commit()
    conn.close()
    return JsonResponse({'success': True, 'message': 'Код отправлен'})


CURRENCIES = ['RUB', 'USD', 'EUR', 'BYN', 'UAH', 'KZT', 'UZS', 'TON', 'USDT', 'STARS']

AVATAR_DIR = os.path.join(settings.MEDIA_ROOT, 'avatars')
DEAL_ATTACHMENTS_DIR = os.path.join(settings.MEDIA_ROOT, 'deal_attachments')
os.makedirs(DEAL_ATTACHMENTS_DIR, exist_ok=True)

def _ensure_known_devices_table():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS known_devices (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     BIGINT NOT NULL,
            ip_address  TEXT NOT NULL,
            user_agent  TEXT NOT NULL DEFAULT '',
            session_key TEXT,
            first_seen  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        cur.execute("SELECT session_key FROM known_devices LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE known_devices ADD COLUMN session_key TEXT")
    conn.commit()
    conn.close()

def _ensure_incidents_table():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'outage'
                        CHECK(status IN ('operational','degraded','outage')),
            description TEXT,
            started_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def _ensure_avatar_column():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT avatar FROM users LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE users ADD COLUMN avatar TEXT DEFAULT ''")
        conn.commit()
    conn.close()

def _ensure_verification_columns():
    conn = get_db()
    cur = conn.cursor()
    for col, col_type in [('is_verified_partner', 'INTEGER DEFAULT 0'), ('verified_at', 'TIMESTAMP'), ('verified_by', 'INTEGER'), ('verified_reason', 'TEXT')]:
        try:
            cur.execute(f"SELECT {col} FROM users LIMIT 1")
        except sqlite3.OperationalError:
            cur.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
    try:
        cur.execute("SELECT id FROM verification_history LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS verification_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                action      TEXT NOT NULL,
                reason      TEXT,
                admin_id    INTEGER NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_verification_history_user
            ON verification_history(user_id, created_at DESC)
        """)
    try:
        cur.execute("SELECT id FROM verification_applications LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS verification_applications (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                category    TEXT NOT NULL,
                description TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pending',
                admin_reason TEXT,
                admin_id    INTEGER,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_verif_apps_user
            ON verification_applications(user_id, created_at DESC)
        """)
    conn.commit()
    conn.close()

def _avatar_url(user_id):
    return f'/usersite/avatar/{user_id}/'
CURRENCY_SYMBOLS = {'RUB': '₽', 'USD': '$', 'EUR': '€', 'BYN': 'Br', 'UAH': '₴', 'KZT': '₸', 'UZS': "so'm", 'TON': 'TON', 'USDT': 'USDT', 'STARS': '★'}
EXCHANGE_RATES = {'RUB': 1, 'USD': 77.5, 'EUR': 88.1, 'BYN': 26.5, 'UAH': 1.7, 'KZT': 0.16, 'UZS': 0.0065, 'TON': 130, 'USDT': 77.5, 'STARS': 2.0}
TIER_BADGES = {'free': 'FREE', 'premium': 'PREMIUM', 'platinum': 'PLATINUM', 'vip': 'VIP'}
TIER_COMMISSION = {'free': 4, 'premium': 2, 'platinum': 1, 'vip': 0}

@safe_db
def dashboard_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')

    user_id = request.session.get('user_id')
    user_dict = {}
    balances = []; total_rub = 0
    deals = []; purchases = 0; sales = 0; total_earned = 0
    referrals_count = 0; active_deals = 0; tickets_count = 0; open_tickets = 0
    notifications = []; unread_notifications = 0; top_sellers = []
    tier = 'free'; tier_active = False; premium_until = None
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        user = cur.fetchone()
        user_dict = dict(user) if user else {}
        if user_dict and 'user_id' in user_dict:
            user_dict['telegram_id'] = user_dict.pop('user_id')

        cur.execute("SELECT * FROM deals WHERE buyer=? OR seller=? ORDER BY created DESC LIMIT 5", (user_id, user_id))
        deals = [dict(d) for d in cur.fetchall()]

        cur.execute("SELECT COUNT(*) FROM deals WHERE buyer=? AND status='completed'", (user_id,))
        purchases = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM deals WHERE seller=? AND status='completed'", (user_id,))
        sales = cur.fetchone()[0] or 0
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM deals WHERE seller=? AND status='completed'", (user_id,))
        total_earned = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM users WHERE referred_by=?", (user_id,))
        referrals_count = cur.fetchone()[0] or 0

        for c in CURRENCIES:
            b = float(user_dict.get(f'balance_{c}', 0) or 0)
            rate = EXCHANGE_RATES.get(c, 1)
            total_rub += b * rate
            balances.append({'currency': c, 'symbol': CURRENCY_SYMBOLS.get(c, c), 'amount': b, 'rub_value': b * rate})

        cur.execute("SELECT COUNT(*) FROM deals WHERE (buyer=? OR seller=?) AND status='awaiting'", (user_id, user_id))
        active_deals = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM support_tickets WHERE user_id=?", (user_id,))
        tickets_count = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM support_tickets WHERE user_id=? AND status='open'", (user_id,))
        open_tickets = cur.fetchone()[0] or 0

        try:
            cur.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 5", (user_id,))
            notifications = [dict(n) for n in cur.fetchall()]
            cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0", (user_id,))
            unread_notifications = cur.fetchone()[0] or 0
        except Exception:
            notifications = []; unread_notifications = 0

        cur.execute("""
            SELECT u.user_id, u.username,
                   COALESCE(AVG(r.rating), 0) as avg_rating,
                   COUNT(r.id) as reviews_count,
                   (SELECT COUNT(*) FROM deals WHERE seller = u.user_id AND status = 'completed') as completed_deals
            FROM users u
            LEFT JOIN reviews r ON r.reviewed_id = u.user_id
            WHERE (SELECT COUNT(*) FROM deals WHERE seller = u.user_id AND status = 'completed') > 0
            GROUP BY u.user_id
            ORDER BY avg_rating DESC, completed_deals DESC
            LIMIT 10
        """)
        top_sellers = [dict(zip([desc[0] for desc in cur.description], row)) for row in cur.fetchall()]
        conn.close()
    except Exception as e:
        print(f"Ошибка dashboard: {e}")

    tier = user_dict.get('premium_tier', 'free') or 'free'
    premium_until = user_dict.get('premium_until', None)
    tier_active = tier != 'free'
    if tier_active and premium_until:
        try:
            tier_active = datetime.fromisoformat(premium_until.replace('Z', '')) > datetime.now()
        except:
            pass
    if not tier_active and tier != 'free':
        tier = 'free'

    tier_badge = TIER_BADGES.get(tier, '⬜ FREE')
    tier_commission = TIER_COMMISSION.get(tier, 4)
    bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', 'NovixGiftBot')

    return render(request, 'usersite/dashboard.html', {
        'user': user_dict, 'deals': deals, 'balances': balances,
        'total_rub': round(total_rub, 2), 'purchases': purchases, 'sales': sales,
        'total_earned': total_earned, 'referrals_count': referrals_count,
        'tier': tier, 'tier_badge': tier_badge, 'tier_commission': tier_commission,
        'tier_active': tier_active, 'premium_until': premium_until,
        'active_deals': active_deals, 'tickets_count': tickets_count,
        'open_tickets': open_tickets, 'notifications': notifications,
        'unread_notifications': unread_notifications, 'top_sellers': top_sellers,
        'bot_username': bot_username, 'now': datetime.now(),
    })


@safe_db
def profile_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')
    _ensure_avatar_column()

    user_id = request.session.get('user_id')
    user = None; deals = []; referrals = []; reviews = []; avg_rating = 0
    notifications = []; unread_notifications = 0
    purchases = 0; sales = 0; active_deals = 0; tickets_count = 0
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        user = cur.fetchone()
        if user:
            user_dict_raw = dict(user)
            user_dict_raw['telegram_id'] = user_dict_raw.pop('user_id')
            user = user_dict_raw

        cur.execute("SELECT * FROM deals WHERE buyer=? OR seller=? ORDER BY created DESC LIMIT 20", (user_id, user_id))
        deals = [dict(d) for d in cur.fetchall()]

        cur.execute("SELECT COUNT(*) FROM deals WHERE buyer=? AND status='completed'", (user_id,))
        purchases = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM deals WHERE seller=? AND status='completed'", (user_id,))
        sales = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM deals WHERE (buyer=? OR seller=?) AND status='awaiting'", (user_id, user_id))
        active_deals = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM support_tickets WHERE user_id=?", (user_id,))
        tickets_count = cur.fetchone()[0] or 0

        cur.execute("SELECT * FROM users WHERE referred_by=?", (user_id,))
        referrals = [dict(r) for r in cur.fetchall()]

        referral_earnings_rub = float(user_dict_raw.get('referral_earnings', 0) or 0) if user else 0
        referral_earnings_level2 = float(user_dict_raw.get('referral_earnings_level2', 0) or 0) if user else 0
        try:
            cur.execute("SELECT COALESCE(SUM(reward_amount), 0) FROM referral_commission_log WHERE referrer_id=?", (user_id,))
            referral_total_commission = cur.fetchone()[0] or 0
        except Exception:
            referral_total_commission = 0
        try:
            cur.execute("SELECT COALESCE(SUM(reward_amount), 0) FROM referral_deposit_log WHERE referrer_id=?", (user_id,))
            referral_deposit_total = cur.fetchone()[0] or 0
        except Exception:
            referral_deposit_total = 0
        try:
            cur.execute("SELECT COUNT(*) FROM referral_level2_log WHERE level1_id=?", (user_id,))
            referral_level2_count = cur.fetchone()[0] or 0
        except Exception:
            referral_level2_count = 0
        try:
            cur.execute("SELECT COUNT(*) FROM referral_commission_log WHERE referrer_id=?", (user_id,))
            referral_commission_count = cur.fetchone()[0] or 0
        except Exception:
            referral_commission_count = 0

        cur.execute("SELECT r.*, d.item AS deal_item FROM reviews r LEFT JOIN deals d ON r.deal_id=d.id WHERE r.reviewed_id=? ORDER BY r.created_at DESC LIMIT 10", (user_id,))
        reviews = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT AVG(rating) FROM reviews WHERE reviewed_id=?", (user_id,))
        avg_rating = cur.fetchone()[0] or 0

        try:
            cur.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 5", (user_id,))
            notifications = [dict(n) for n in cur.fetchall()]
            cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0", (user_id,))
            unread_notifications = cur.fetchone()[0] or 0
        except Exception:
            notifications = []; unread_notifications = 0

        conn.close()
    except Exception as e:
        print(f"Ошибка profile: {e}")

    tier = (user and (user['premium_tier'] or 'free')) or 'free'
    premium_until = user and user['premium_until']
    tier_active = tier != 'free'
    days_left = 0
    premium_until_dt = None
    if tier_active and premium_until:
        try:
            expiry = datetime.fromisoformat(premium_until.replace('Z', ''))
            premium_until_dt = expiry.replace(tzinfo=None) + timedelta(hours=3)
            tier_active = expiry > datetime.now()
            days_left = max(0, (expiry - datetime.now()).days)
        except:
            pass
    if not tier_active and tier != 'free':
        tier = 'free'

    tier_badge = TIER_BADGES.get(tier, '⬜ FREE')
    tier_commission = TIER_COMMISSION.get(tier, 4)
    bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', 'NovixGiftBot')

    total_rub = 0
    balances = []
    created_at_dt = None
    if user:
        for c in CURRENCIES:
            b = float(user.get(f'balance_{c}', 0) or 0)
            rate = EXCHANGE_RATES.get(c, 1)
            total_rub += b * rate
            balances.append({'currency': c, 'symbol': CURRENCY_SYMBOLS.get(c, c), 'amount': b, 'rub_value': b * rate})
        if user.get('created_at'):
            try:
                created_at_dt = datetime.fromisoformat(user['created_at'].replace('Z', '')) + timedelta(hours=3)
            except:
                pass

    avatar_url = _avatar_url(user_id) if user and user.get('avatar') else None

    volume_tier_info = _get_vol_tier_info(user_id)

    return render(request, 'usersite/profile.html', {
        'user': dict(user) if user else None,
        'deals': deals, 'referrals': referrals, 'reviews': reviews,
        'avg_rating': round(avg_rating, 1),
        'balances': balances, 'total_rub': round(total_rub, 2),
        'tier': tier, 'tier_badge': tier_badge, 'tier_commission': tier_commission,
        'tier_active': tier_active, 'days_left': days_left,
        'premium_until_dt': premium_until_dt,
        'created_at_dt': created_at_dt,
        'bot_username': bot_username, 'now': datetime.now(),
        'avatar_url': avatar_url,
        'notifications': notifications, 'unread_notifications': unread_notifications,
        'purchases': purchases, 'sales': sales,
        'active_deals': active_deals, 'tickets_count': tickets_count,
        'referral_earnings_rub': referral_earnings_rub,
        'referral_earnings_level2': referral_earnings_level2,
        'referral_total_commission': referral_total_commission,
        'referral_deposit_total': referral_deposit_total,
        'referral_level2_count': referral_level2_count,
        'referral_commission_count': referral_commission_count,
        'referral_total_all': referral_total_commission + referral_deposit_total + referral_earnings_rub,
        'volume_tier_info': volume_tier_info,
    })


def settings_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')
    user_id = request.session.get('user_id')
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT profile_email, username FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    ctx = {'telegram_id': str(user_id), 'profile_email': '', 'username': ''}
    if row:
        ctx = {'telegram_id': str(user_id), 'profile_email': row[0] or '', 'username': row[1] or ''}
    return render(request, 'usersite/settings.html', ctx)


@csrf_exempt
@require_http_methods(["POST"])
def api_update_profile(request):
    try:
        if not check_auth(request):
            return JsonResponse({'success': False, 'error': 'Не авторизован'}, status=401)
        uid = request.session.get('user_id')
        if not uid:
            return JsonResponse({'success': False, 'error': 'ID пользователя не найден'}, status=400)
        data = json.loads(request.body)
        email = data.get('email', '').strip()
        name = data.get('name', '').strip()
        password = data.get('password', '')

        if email and not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
            return JsonResponse({'success': False, 'error': 'Некорректный email'}, status=400)

        conn = get_db()
        cur = conn.cursor()

        updates = ["profile_email=?", "username=?"]
        vals = [email or None, name or None]

        if password:
            if len(password) < 6:
                conn.close()
                return JsonResponse({'success': False, 'error': 'Пароль минимум 6 символов'}, status=400)
            updates.append("profile_password_hash=?")
            vals.append(make_password(password))

        vals.append(uid)
        cur.execute(f"UPDATE users SET {', '.join(updates)} WHERE user_id=?", vals)
        conn.commit()
        conn.close()

        changed = []
        if password:
            log_user_action(uid, 'password_changed', 'Изменён пароль в настройках', request)
            changed.append('пароль')
        profile_parts = []
        if email:
            profile_parts.append(f'email: {email}')
        if name:
            profile_parts.append(f'username: {name}')
        if profile_parts:
            log_user_action(uid, 'profile_updated', ', '.join(profile_parts), request)

        if name:
            request.session['username'] = name
            request.session.modified = True
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def _get_seller_stats(seller_id: int) -> dict:
    """Возвращает статистику продавца: число сделок, объём, среднее время подтверждения."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS cnt, COALESCE(SUM(amount), 0) AS total_vol, "
        "AVG((julianday(COALESCE(completed, created)) - julianday(created)) * 24) AS avg_hours "
        "FROM deals WHERE seller=? AND status='completed'",
        (seller_id,)
    )
    row = cur.fetchone()
    cnt = row[0] or 0
    total_vol_rub = 0
    if cnt:
        cur.execute("SELECT amount, currency FROM deals WHERE seller=? AND status='completed'", (seller_id,))
        for r in cur.fetchall():
            total_vol_rub += r[0] * EXCHANGE_RATES.get(r[1], 1)
    avg_hours = row[2] or 0
    conn.close()
    return {
        'completed_deals': cnt,
        'total_volume_rub': round(total_vol_rub, 2),
        'avg_confirm_hours': round(avg_hours, 1),
    }


def public_profile_view(request, username):
    _ensure_avatar_column()
    _ensure_profile_reviews_table()
    user = None; deals = []
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=?", (username,))
        row = cur.fetchone()
        if row:
            u = dict(row)
            created_at_dt = None
            if u.get('created_at'):
                try:
                    created_at_dt = datetime.fromisoformat(u['created_at'].replace('Z', '')) + timedelta(hours=3)
                except:
                    pass
            premium_until_dt = None
            if u.get('premium_until'):
                try:
                    premium_until_dt = datetime.fromisoformat(u['premium_until'].replace('Z', '')) + timedelta(hours=3)
                except:
                    pass
            user = {
                'telegram_id': u.get('user_id'),
                'username': u.get('username'),
                'created_at': u.get('created_at'),
                'created_at_dt': created_at_dt,
                'premium_tier': u.get('premium_tier') or 'free',
                'rating': u.get('rating') or 0,
                'reviews_count': u.get('reviews_count') or 0,
                'premium_until': u.get('premium_until'),
                'premium_until_dt': premium_until_dt,
                'referral_code': u.get('referral_code'),
                'referred_by': u.get('referred_by'),
            }
            balances = []
            total_rub = 0
            for c in CURRENCIES:
                b = float(u.get(f'balance_{c}', 0) or 0)
                rate = EXCHANGE_RATES.get(c, 1)
                total_rub += b * rate
                if b > 0:
                    balances.append({'currency': c, 'symbol': CURRENCY_SYMBOLS.get(c, c), 'amount': b, 'rub_value': b * rate})

            tier = user['premium_tier']
            premium_until = user.get('premium_until')
            tier_active = tier != 'free'
            days_left = 0
            if tier_active and premium_until:
                try:
                    expiry = datetime.fromisoformat(premium_until.replace('Z', ''))
                    tier_active = expiry > datetime.now()
                    days_left = max(0, (expiry - datetime.now()).days)
                except:
                    pass
            if not tier_active and tier != 'free':
                tier = 'free'

            cur.execute("SELECT * FROM deals WHERE buyer=? OR seller=? ORDER BY created DESC LIMIT 20", (u['user_id'], u['user_id']))
            deals = [dict(d) for d in cur.fetchall()]

            # Profile reviews
            cur.execute(
                "SELECT pr.*, u.username AS reviewer_name FROM profile_reviews pr "
                "LEFT JOIN users u ON pr.reviewer_id = u.user_id "
                "WHERE pr.reviewed_id=? ORDER BY pr.created_at DESC LIMIT 20",
                (u['user_id'],)
            )
            profile_reviews = [dict(r) for r in cur.fetchall()]

            cur.execute("SELECT AVG(rating) FROM profile_reviews WHERE reviewed_id=?", (u['user_id'],))
            avg_rating = cur.fetchone()[0] or 0

            # Check if current user has already reviewed
            current_user_id = request.session.get('user_id')
            has_reviewed = False
            my_profile_review = None
            if current_user_id:
                cur.execute("SELECT id FROM profile_reviews WHERE reviewer_id=? AND reviewed_id=?", (current_user_id, u['user_id']))
                row = cur.fetchone()
                has_reviewed = row is not None
                if has_reviewed:
                    cur.execute("SELECT * FROM profile_reviews WHERE id=?", (row[0],))
                    my_profile_review = dict(cur.fetchone()) if cur.fetchone() else None

            seller_stats = _get_seller_stats(u['user_id'])
            avatar_url = _avatar_url(u.get('user_id')) if u.get('avatar') else None
            conn.close()
            return render(request, 'usersite/profile_public.html', {
                'user': user, 'deals': deals, 'balances': balances, 'total_rub': round(total_rub, 2),
                'tier': tier, 'tier_badge': TIER_BADGES.get(tier, '⬜ FREE'),
                'tier_active': tier_active, 'days_left': days_left,
                'avg_rating': round(avg_rating, 1), 'now': datetime.now(),
                'avatar_url': avatar_url,
                'profile_reviews': profile_reviews,
                'has_reviewed': has_reviewed,
                'my_profile_review': my_profile_review,
                'seller_stats': seller_stats,
            })
        conn.close()
    except Exception as e:
        print(f"public_profile error: {e}")
    return render(request, 'usersite/profile_public.html', {'user': None, 'deals': [], 'balances': [], 'total_rub': 0})


def forbes_view(request):
    _ensure_avatar_column()
    page = request.GET.get('page', 1)
    try:
        page = int(page)
    except:
        page = 1
    per_page = 50
    offset = (page - 1) * per_page

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0] or 0
    total_pages = max(1, (total_users + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
        offset = (page - 1) * per_page

    balance_cols = ' + '.join([f"COALESCE(balance_{c},0)" for c in CURRENCIES])
    cur.execute(f"""
        SELECT user_id, username, premium_tier, avatar, {balance_cols} as total_balance
        FROM users ORDER BY total_balance DESC, user_id ASC LIMIT ? OFFSET ?
    """, (per_page, offset))
    rows = cur.fetchall()
    rankings = []
    for i, row in enumerate(rows):
        rankings.append({
            'rank': offset + i + 1,
            'user_id': row[0],
            'username': row[1] or f"ID{row[0]}",
            'premium_tier': row[2] or 'free',
            'avatar_url': _avatar_url(row[0]) if row[3] else None,
            'total_balance': round(row[4], 2) if row[4] else 0,
        })
    conn.close()

    return render(request, 'usersite/top.html', {
        'rankings': rankings, 'page': page, 'total_pages': total_pages,
        'total_users': total_users,
    })


def logout_view(request):
    request.session.flush()
    return redirect('/usersite/login/')


def user_profile_redirect(request, user_id):
    if request.session.get('admin_role') is None:
        return redirect('/usersite/profile/')
    return redirect(f'/users/{user_id}/')


PREMIUM_DURATIONS = [
    (30, 1, 0),
    (90, 3, 5),
    (180, 6, 10),
    (365, 12, 20),
]

def calc_tier_price_site(tier: str, days: int) -> float:
    prices = {'premium': 299, 'platinum': 599, 'vip': 1499}
    pm = prices.get(tier, 0)
    for d, m, disc in PREMIUM_DURATIONS:
        if d == days:
            return pm * m * (1 - disc / 100)
    return pm * (days / 30)

TIER_BADGES_MAP = {'premium': 'PREMIUM', 'platinum': 'PLATINUM', 'vip': 'VIP'}
TIER_LABELS_SITE = {'premium': 'Premium', 'platinum': 'Platinum', 'vip': 'VIP-статус'}

@safe_db
def premium_wizard_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')
    user_id = request.session.get('user_id')
    step = request.GET.get('step', '1')

    # ─── Шаг 1: выбор тарифа ────────────────────────────────────────
    if step == '1':
        if request.method == 'POST':
            tier = request.POST.get('tier')
            if tier in ('premium', 'platinum', 'vip'):
                request.session['wizard_tier'] = tier
                return redirect('/usersite/premium/?step=2')
        tiers = [
            {'id': 'premium', 'label': '⭐ Premium', 'price': '299₽/мес', 'commission': '2%', 'desc': 'Высокий приоритет'},
            {'id': 'platinum', 'label': '💎 Platinum', 'price': '599₽/мес', 'commission': '1%', 'desc': 'Мгновенный приоритет'},
            {'id': 'vip', 'label': '👑 VIP-статус', 'price': '1499₽/мес', 'commission': '0%', 'desc': '24/7 Личный менеджер'},
        ]
        return render(request, 'usersite/premium_wizard.html', {'step': '1', 'tiers': tiers})

    # ─── Шаг 2: выбор валюты ────────────────────────────────────────
    wizard_tier = request.session.get('wizard_tier')
    if not wizard_tier:
        return redirect('/usersite/premium/?step=1')
    if step == '2':
        if request.method == 'POST':
            currency = request.POST.get('currency')
            if currency:
                request.session['wizard_currency'] = currency
                return redirect('/usersite/premium/?step=3')
        currencies = [
            {'id': 'RUB', 'symbol': '₽', 'name': 'RUB'},
            {'id': 'USDT', 'symbol': '💵', 'name': 'USDT'},
            {'id': 'STARS', 'symbol': '⭐', 'name': 'STARS'},
        ]
        return render(request, 'usersite/premium_wizard.html', {'step': '2', 'tier': wizard_tier, 'tier_label': TIER_LABELS_SITE.get(wizard_tier, wizard_tier), 'currencies': currencies})

    # ─── Шаг 3: выбор длительности ──────────────────────────────────
    wizard_currency = request.session.get('wizard_currency')
    if not wizard_currency:
        return redirect('/usersite/premium/?step=2')
    if step == '3':
        if request.method == 'POST':
            days = request.POST.get('days')
            if days:
                request.session['wizard_days'] = int(days)
                return redirect('/usersite/premium/?step=confirm')

        rates = EXCHANGE_RATES
        rate = rates.get(wizard_currency, 1)
        price_month = {'premium': 299, 'platinum': 599, 'vip': 1499}.get(wizard_tier, 0)
        durations = []
        for days, months, discount in PREMIUM_DURATIONS:
            total_rub = price_month * months * (1 - discount / 100)
            price_in_currency = total_rub / rate if rate > 0 else total_rub
            label_d = f"{months} мес." if months > 1 else "1 месяц"
            if discount:
                label_d += f" (-{discount}%)"
            durations.append({'days': days, 'label': label_d, 'price': f"{price_in_currency:,.2f}".rstrip('0').rstrip('.'), 'currency': wizard_currency})
        return render(request, 'usersite/premium_wizard.html', {'step': '3', 'tier': wizard_tier, 'tier_label': TIER_LABELS_SITE.get(wizard_tier, wizard_tier), 'currency': wizard_currency, 'durations': durations})

    # ─── Подтверждение ──────────────────────────────────────────────
    wizard_days = request.session.get('wizard_days')
    if not all([wizard_tier, wizard_currency, wizard_days]):
        return redirect('/usersite/premium/?step=1')

    if step == 'confirm':
        if request.method == 'POST':
            action = request.POST.get('action')
            if action == 'pay':
                total_rub = calc_tier_price_site(wizard_tier, wizard_days)
                rate = EXCHANGE_RATES.get(wizard_currency, 1)
                price_in_currency = total_rub / rate if rate > 0 else total_rub
                deduction_plan = {}
                try:
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
                    user = cur.fetchone()
                    if not user:
                        conn.close()
                        return render(request, 'usersite/premium_wizard.html', {'step': 'error', 'error': 'Пользователь не найден'})

                    bal_col = f"balance_{wizard_currency}"
                    if bal_col not in ('balance_RUB', 'balance_USDT', 'balance_STARS'):
                        conn.close()
                        return render(request, 'usersite/premium_wizard.html', {'step': 'error', 'error': 'Валюта не поддерживается'})

                    cur.execute(f"SELECT {bal_col} FROM users WHERE user_id=?", (user_id,))
                    current_bal = (cur.fetchone() or [0])[0] or 0

                    if current_bal >= price_in_currency:
                        expires = (datetime.now() + timedelta(days=wizard_days)).strftime('%Y-%m-%d %H:%M:%S')
                        cur.execute("BEGIN IMMEDIATE")
                        cur.execute(f"UPDATE users SET {bal_col} = {bal_col} - ? WHERE user_id=?", (price_in_currency, user_id))
                        cur.execute("UPDATE users SET premium_tier=?, premium_until=? WHERE user_id=?", (wizard_tier, expires, user_id))
                        conn.commit()
                        conn.close()
                        for k in ['wizard_tier', 'wizard_currency', 'wizard_days']:
                            request.session.pop(k, None)
                        tier_badge = TIER_BADGES_MAP.get(wizard_tier, wizard_tier)
                        return render(request, 'usersite/premium_wizard.html', {'step': 'success', 'tier_badge': tier_badge, 'days': wizard_days, 'price': f"{fmt_price_site(price_in_currency)} {wizard_currency}"})

                    total_user_rub = 0
                    balances = {}
                    for c in CURRENCIES:
                        b = float(user.get(f'balance_{c}', 0) or 0)
                        balances[c] = b
                        total_user_rub += b * EXCHANGE_RATES.get(c, 1)

                    if total_user_rub < total_rub:
                        conn.close()
                        return render(request, 'usersite/premium_wizard.html', {'step': 'error', 'error': f'Недостаточно средств. Нужно ≈{total_rub:.0f} RUB, доступно ≈{total_user_rub:.0f} RUB'})

                    remaining_rub = total_rub
                    deduction_order = ["RUB", "USDT", "STARS"]
                    cur.execute("BEGIN IMMEDIATE")
                    for c in deduction_order:
                        if remaining_rub <= 0:
                            break
                        bal = balances.get(c, 0)
                        if bal <= 0:
                            continue
                        if c == "RUB":
                            deduct = min(bal, remaining_rub)
                            cur.execute("UPDATE users SET balance_RUB = balance_RUB - ? WHERE user_id=?", (deduct, user_id))
                            deduction_plan[c] = deduct
                            remaining_rub -= deduct
                        else:
                            cr = EXCHANGE_RATES.get(c, 1)
                            if cr <= 0:
                                continue
                            needed = remaining_rub / cr
                            if bal >= needed:
                                cur.execute(f"UPDATE users SET balance_{c} = balance_{c} - ? WHERE user_id=?", (round(needed, 6), user_id))
                                deduction_plan[c] = round(needed, 6)
                                remaining_rub = 0
                            else:
                                cur.execute(f"UPDATE users SET balance_{c} = balance_{c} - ? WHERE user_id=?", (bal, user_id))
                                deduction_plan[c] = bal
                                remaining_rub -= bal * cr

                    if remaining_rub > 0:
                        conn.rollback()
                        conn.close()
                        return render(request, 'usersite/premium_wizard.html', {'step': 'error', 'error': 'Ошибка списания'})

                    expires = (datetime.now() + timedelta(days=wizard_days)).strftime('%Y-%m-%d %H:%M:%S')
                    cur.execute("UPDATE users SET premium_tier=?, premium_until=? WHERE user_id=?", (wizard_tier, expires, user_id))
                    conn.commit()
                    conn.close()

                    for k in ['wizard_tier', 'wizard_currency', 'wizard_days']:
                        request.session.pop(k, None)
                    tier_badge = TIER_BADGES_MAP.get(wizard_tier, wizard_tier)
                    plan_parts = [f"{fmt_price_site(a)} {c}" for c, a in deduction_plan.items()]
                    return render(request, 'usersite/premium_wizard.html', {'step': 'success', 'tier_badge': tier_badge, 'days': wizard_days, 'cross_plan': ' + '.join(plan_parts), 'total_rub': f'{total_rub:.0f}'})
                except Exception as e:
                    print(f"Ошибка premium: {e}")
                    return render(request, 'usersite/premium_wizard.html', {'step': 'error', 'error': f'Ошибка: {e}'})

            return redirect('/usersite/premium/?step=1')

        # GET — показываем подтверждение
        total_rub = calc_tier_price_site(wizard_tier, wizard_days)
        rate = EXCHANGE_RATES.get(wizard_currency, 1)
        price_in_currency = total_rub / rate if rate > 0 else total_rub
        tier_badge = TIER_BADGES_MAP.get(wizard_tier, wizard_tier)
        return render(request, 'usersite/premium_wizard.html', {'step': 'confirm', 'tier': wizard_tier, 'tier_badge': tier_badge, 'tier_label': TIER_LABELS_SITE.get(wizard_tier, wizard_tier), 'currency': wizard_currency, 'days': wizard_days, 'price': f"{fmt_price_site(price_in_currency)} {wizard_currency}", 'total_rub': f'{total_rub:.0f}'})

    return redirect('/usersite/premium/?step=1')


def fmt_price_site(v: float) -> str:
    if v >= 100:
        return f"{int(round(v))}"
    return f"{v:.2f}".rstrip('0').rstrip('.')


# ============= TICKET PAGES =============

def user_tickets_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')
    user_id = request.session.get('user_id')
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM support_tickets WHERE user_id=? ORDER BY updated_at DESC", (user_id,))
    tickets = [dict(t) for t in cur.fetchall()]
    conn.close()
    return render(request, 'usersite/tickets.html', {'tickets': tickets})


def user_ticket_new_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')
    return render(request, 'usersite/ticket_new.html')


def user_ticket_detail_view(request, ticket_id):
    if not check_auth(request):
        return redirect('/usersite/login/')
    user_id = request.session.get('user_id')
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM support_tickets WHERE id=? AND user_id=?", (ticket_id, user_id))
    ticket = cur.fetchone()
    if not ticket:
        conn.close()
        return redirect('/usersite/tickets/')
    cur.execute("SELECT * FROM support_ticket_messages WHERE ticket_id=? ORDER BY created_at", (ticket_id,))
    messages = []
    for m in cur.fetchall():
        md = dict(m)
        raw = (md.get('attachments') or '').strip()
        md['attachments_list'] = [a for a in raw.split(',') if a] if raw else []
        messages.append(md)

    creator_login = None
    cur.execute("SELECT username FROM users WHERE user_id=?", (ticket['user_id'],))
    row = cur.fetchone()
    if row:
        creator_login = row[0]

    viewer_is_admin = request.session.get('admin_role') is not None

    conn.close()
    return render(request, 'usersite/ticket_detail.html', {
        'ticket': dict(ticket),
        'messages': messages,
        'creator_login': creator_login,
        'viewer_is_admin': viewer_is_admin,
    })


# ============= TICKET API =============

@csrf_exempt
def create_ticket(request):
    if not check_auth(request):
        return JsonResponse({'success': False, 'error': 'Not logged in'}, status=401)
    user_id = request.session.get('user_id')
    subject = request.POST.get('subject', '')
    category = request.POST.get('category', subject)
    message = request.POST.get('message', '')
    user_login = request.POST.get('user_login', '')
    order_number = request.POST.get('order_number', '')
    user_type = request.POST.get('user_type', 'buyer')
    conn = get_db()
    cur = conn.cursor()

    # VIP routing
    cur.execute("SELECT premium_tier FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    tier = (row and row[0]) or 'free'
    is_vip = tier == 'vip'
    vip_tag = '[VIP] ' if is_vip else ''

    cur.execute(
        "INSERT INTO support_tickets (user_id, subject, category, user_type, order_number) VALUES (?,?,?,?,?)",
        (user_id, f"{vip_tag}{subject}", category, user_type, order_number)
    )
    ticket_id = cur.lastrowid
    if not user_login:
        cur.execute("SELECT username FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        user_login = row[0] if row else str(user_id)

    # Save file attachments
    attachments = []
    media_dir = os.path.join(settings.MEDIA_ROOT, 'tickets', str(ticket_id))
    os.makedirs(media_dir, exist_ok=True)
    files = request.FILES.getlist('attachments')
    for f in files:
        ext = os.path.splitext(f.name)[1] or ''
        safe_name = f"{len(attachments)}_{int(time.time())}{ext}"
        dest = os.path.join(media_dir, safe_name)
        with open(dest, 'wb+') as out:
            for chunk in f.chunks():
                out.write(chunk)
        attachments.append(f"media/tickets/{ticket_id}/{safe_name}")

    cur.execute(
        "INSERT INTO support_ticket_messages (ticket_id, sender_type, sender_name, message, attachments) VALUES (?,'user',?,?,?)",
        (ticket_id, user_login, message, ','.join(attachments))
    )

    if is_vip:
        cur.execute("UPDATE support_tickets SET assigned_to=? WHERE id=?", (str(OWNER_TELEGRAM_ID), ticket_id))

    conn.commit()
    conn.close()
    return JsonResponse({'success': True, 'ticket_id': ticket_id, 'vip': is_vip})


@csrf_exempt
def add_ticket_reply(request, ticket_id):
    if not check_auth(request):
        return JsonResponse({'success': False, 'error': 'Not logged in'}, status=401)
    user_id = request.session.get('user_id')
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
    cur.execute("SELECT username FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    sender_name = row[0] if row else str(user_id)
    cur.execute(
        "INSERT INTO support_ticket_messages (ticket_id, sender_type, sender_name, message) VALUES (?,'user',?,?)",
        (ticket_id, sender_name, message)
    )
    cur.execute("UPDATE support_tickets SET updated_at=datetime('now') WHERE id=?", (ticket_id,))
    conn.commit()
    conn.close()
    return JsonResponse({'success': True})


@csrf_exempt
def close_ticket(request, ticket_id):
    if not check_auth(request):
        return JsonResponse({'success': False, 'error': 'Not logged in'}, status=401)
    user_id = request.session.get('user_id')
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


@safe_db
def transactions_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')
    user_id = request.session.get('user_id')
    transactions = []
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY created_at DESC LIMIT 50", (user_id,))
        for t in cur.fetchall():
            row = dict(t)
            enc = row.get('encrypted_meta') or ''
            if enc and _enc_enabled():
                try:
                    import json as _json
                    dec = decrypt_value(enc)
                    meta = _json.loads(dec)
                    row['amount'] = meta.get('amount', row.get('amount'))
                    row['description'] = meta.get('desc', row.get('description'))
                except Exception:
                    pass
            transactions.append(row)
        conn.close()
    except Exception as e:
        print(f"Ошибка transactions: {e}")
        transactions = []
    return render(request, 'usersite/transactions.html', {
        'transactions': transactions,
    })


def withdraw_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')
    user_id = request.session.get('user_id')
    balances = {}; total_rub = 0; requests = []
    tier = 'free'; commission_pct = 4; net_rub = 0
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        user = cur.fetchone()
        if user:
            u = dict(user)
            tier = (u.get('premium_tier') or 'free')
            commission_pct = TIER_COMMISSION.get(tier, 4)
            for c in CURRENCIES:
                val = float(u.get(f'balance_{c}', 0) or 0)
                if val > 0:
                    rate = EXCHANGE_RATES.get(c, 1)
                    rub_val = val * rate
                    balances[c] = {'amount': val, 'symbol': CURRENCY_SYMBOLS.get(c, c), 'rub_value': rub_val}
                    total_rub += rub_val
            net_rub = round(total_rub * (1 - commission_pct / 100), 2)
        cur.execute("SELECT * FROM withdrawal_requests WHERE user_id=? ORDER BY created_at DESC LIMIT 20", (user_id,))
        requests = [dict(r) for r in cur.fetchall()]
        conn.close()
    except Exception as e:
        print(f"Ошибка withdraw: {e}")
    return render(request, 'usersite/withdraw.html', {
        'requests': requests, 'balances': balances,
        'total_rub': round(total_rub, 2), 'tier': tier,
        'tier_badge': TIER_BADGES.get(tier, '⬜ FREE'),
        'commission_pct': commission_pct, 'net_rub': net_rub,
    })


@csrf_exempt
@require_http_methods(["POST"])
def withdraw_create_api(request):
    if not check_auth(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    data = json.loads(request.body)
    user_id = request.session.get('user_id')
    amount = float(data.get('amount', 0))
    wallet_type = data.get('wallet_type', 'card')
    wallet_address = data.get('wallet_address', '')

    if amount <= 0:
        return JsonResponse({'success': False, 'error': 'Сумма должна быть больше 0'})
    if not wallet_address:
        return JsonResponse({'success': False, 'error': 'Укажите реквизиты'})

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        user = cur.fetchone()
        if not user:
            conn.close()
            return JsonResponse({'success': False, 'error': 'Пользователь не найден'}, status=404)
        u = dict(user)

        total_rub = 0
        for c in CURRENCIES:
            val = float(u.get(f'balance_{c}', 0) or 0)
            total_rub += val * EXCHANGE_RATES.get(c, 1)

        tier = u.get('premium_tier', 'free') or 'free'
        commission_pct = TIER_COMMISSION.get(tier, 4)
        net_rub = total_rub * (1 - commission_pct / 100)
    except Exception as e:
        print(f"Ошибка withdraw API: {e}")
        return JsonResponse({'success': False, 'error': 'Ошибка сервера'}, status=500)

    if net_rub < amount:
        conn.close()
        return JsonResponse({
            'success': False,
            'error': f'Недостаточно средств. Доступно к выводу (чистыми): {round(net_rub, 2)} RUB'
        })

    conn.execute(
        "INSERT INTO withdrawal_requests (user_id, amount, wallet_type, wallet_address, status) VALUES (?, ?, ?, ?, 'pending')",
        (user_id, amount, wallet_type, wallet_address)
    )
    conn.commit()
    conn.close()
    return JsonResponse({'success': True, 'message': 'Заявка создана, ожидайте подтверждения'})


# ===================== REVIEWS =====================

def reviews_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')
    user_id = request.session.get('user_id')
    received = []; given = []; avg_rating = 0; total = 0; positive_pct = 0; user = None
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT r.*, d.item AS deal_item, u.username AS reviewer_name FROM reviews r "
                    "LEFT JOIN deals d ON r.deal_id = d.id "
                    "LEFT JOIN users u ON r.reviewer_id = u.user_id "
                    "WHERE r.reviewed_id = ? ORDER BY r.created_at DESC LIMIT 50", (user_id,))
        received = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT r.*, d.item AS deal_item, u.username AS reviewed_name FROM reviews r "
                    "LEFT JOIN deals d ON r.deal_id = d.id "
                    "LEFT JOIN users u ON r.reviewed_id = u.user_id "
                    "WHERE r.reviewer_id = ? ORDER BY r.created_at DESC LIMIT 50", (user_id,))
        given = [dict(r) for r in cur.fetchall()]

        cur.execute(
            "SELECT AVG(rating) as avg_rating, COUNT(*) as total, "
            "SUM(CASE WHEN rating >= 4 THEN 1 ELSE 0 END) as positive "
            "FROM reviews WHERE reviewed_id = ?", (user_id,))
        stats_row = cur.fetchone()
        if stats_row:
            avg_rating = round(stats_row[0] or 0, 1)
            total = stats_row[1] or 0
            positive = stats_row[2] or 0
            positive_pct = round(positive / total * 100, 1) if total > 0 else 0

        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        u = cur.fetchone()
        user = dict(u) if u else {}
        avatar_url = _avatar_url(user_id) if user.get('avatar') else None
        conn.close()
    except Exception as e:
        print(f"Ошибка reviews: {e}")

    return render(request, 'usersite/reviews.html', {
        'user': user, 'received': received, 'given': given,
        'avg_rating': avg_rating, 'total': total, 'positive_pct': positive_pct,
        'avatar_url': avatar_url,
    })


@csrf_exempt
@require_http_methods(["POST"])
def update_review_api(request):
    if not check_auth(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    user_id = request.session.get('user_id')
    data = json.loads(request.body)
    review_id = int(data.get('review_id', 0))
    rating = data.get('rating')
    comment = data.get('comment', '')

    if rating is not None:
        rating = int(rating)
        if rating < 1 or rating > 5:
            return JsonResponse({'success': False, 'error': 'Рейтинг от 1 до 5'})

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT reviewer_id FROM reviews WHERE id = ?", (review_id,))
    row = cur.fetchone()
    if not row or row[0] != user_id:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Отзыв не найден или это не ваш отзыв'})

    sets = []
    params = []
    if rating is not None:
        sets.append("rating = ?")
        params.append(rating)
    if comment is not None:
        sets.append("comment = ?")
        params.append(comment)
    if not sets:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Нет данных для обновления'})
    params.append(review_id)
    cur.execute(f"UPDATE reviews SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return JsonResponse({'success': True, 'message': 'Отзыв обновлён'})


@csrf_exempt
@require_http_methods(["POST"])
def report_review_api(request):
    if not check_auth(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    user_id = request.session.get('user_id')
    data = json.loads(request.body)
    review_id = int(data.get('review_id', 0))
    reason = data.get('reason', '')

    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE reviews SET reported = 1, report_reason = ? WHERE id = ?", (reason, review_id))
    conn.commit()
    conn.close()
    return JsonResponse({'success': True, 'message': 'Жалоба отправлена администрации'})


@csrf_exempt
@require_http_methods(["POST"])
def api_create_profile_review(request):
    if not check_auth(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    _ensure_profile_reviews_table()
    user_id = request.session.get('user_id')
    data = json.loads(request.body)
    reviewed_id = int(data.get('reviewed_id', 0))
    rating = int(data.get('rating', 0))
    comment = data.get('comment', '').strip()
    if reviewed_id == user_id:
        return JsonResponse({'error': 'Нельзя оставить отзыв на себя'}, status=400)
    if rating < 1 or rating > 5:
        return JsonResponse({'error': 'Рейтинг от 1 до 5'}, status=400)
    if not comment:
        return JsonResponse({'error': 'Напишите отзыв'}, status=400)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (reviewed_id,))
    if not cur.fetchone():
        conn.close()
        return JsonResponse({'error': 'Пользователь не найден'}, status=404)
    cur.execute("SELECT id FROM profile_reviews WHERE reviewer_id=? AND reviewed_id=?", (user_id, reviewed_id))
    if cur.fetchone():
        conn.close()
        return JsonResponse({'error': 'Вы уже оставили отзыв этому пользователю'}, status=400)
    cur.execute(
        "INSERT INTO profile_reviews (reviewer_id, reviewed_id, rating, comment) VALUES (?, ?, ?, ?)",
        (user_id, reviewed_id, rating, comment)
    )
    # Update rating stats on the reviewed user
    cur.execute("SELECT AVG(rating), COUNT(*) FROM profile_reviews WHERE reviewed_id=?", (reviewed_id,))
    row = cur.fetchone()
    avg = round(row[0] or rating, 1)
    cnt = row[1] or 1
    cur.execute("UPDATE users SET rating=?, reviews_count=? WHERE user_id=?", (avg, cnt, reviewed_id))
    conn.commit()
    conn.close()
    return JsonResponse({'success': True, 'message': 'Отзыв оставлен'})


@csrf_exempt
@require_http_methods(["POST"])
def api_update_profile_review(request):
    if not check_auth(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    _ensure_profile_reviews_table()
    user_id = request.session.get('user_id')
    data = json.loads(request.body)
    review_id = int(data.get('review_id', 0))
    rating = int(data.get('rating', 0))
    comment = data.get('comment', '').strip()
    if rating < 1 or rating > 5:
        return JsonResponse({'error': 'Рейтинг от 1 до 5'}, status=400)
    if not comment:
        return JsonResponse({'error': 'Напишите отзыв'}, status=400)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT reviewer_id, reviewed_id, created_at FROM profile_reviews WHERE id=?", (review_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return JsonResponse({'error': 'Отзыв не найден'}, status=404)
    if row[0] != user_id:
        conn.close()
        return JsonResponse({'error': 'Это не ваш отзыв'}, status=403)
    reviewed_id = row[1]
    created_at = datetime.fromisoformat(row[2].replace('Z', ''))
    if (datetime.now() - created_at) > timedelta(days=14):
        conn.close()
        return JsonResponse({'error': 'Прошло более 14 дней, отзыв нельзя изменить'}, status=400)
    cur.execute(
        "UPDATE profile_reviews SET rating=?, comment=?, updated_at=datetime('now') WHERE id=?",
        (rating, comment, review_id)
    )
    cur.execute("SELECT AVG(rating) FROM profile_reviews WHERE reviewed_id=?", (reviewed_id,))
    avg = cur.fetchone()[0] or rating
    cur.execute("UPDATE users SET rating=? WHERE user_id=?", (round(avg, 1), reviewed_id))
    conn.commit()
    conn.close()
    return JsonResponse({'success': True, 'message': 'Отзыв обновлён'})


@csrf_exempt
@require_http_methods(["POST"])
def api_delete_profile_review(request):
    if not check_auth(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    _ensure_profile_reviews_table()
    user_id = request.session.get('user_id')
    data = json.loads(request.body)
    review_id = int(data.get('review_id', 0))
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT reviewer_id, reviewed_id FROM profile_reviews WHERE id=?", (review_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return JsonResponse({'error': 'Отзыв не найден'}, status=404)
    if row[0] != user_id:
        conn.close()
        return JsonResponse({'error': 'Это не ваш отзыв'}, status=403)
    reviewed_id = row[1]
    cur.execute("DELETE FROM profile_reviews WHERE id=?", (review_id,))
    cur.execute("SELECT AVG(rating), COUNT(*) FROM profile_reviews WHERE reviewed_id=?", (reviewed_id,))
    row2 = cur.fetchone()
    avg = round(row2[0] or 0, 1)
    cnt = row2[1] or 0
    cur.execute("UPDATE users SET rating=?, reviews_count=? WHERE user_id=?", (avg, cnt, reviewed_id))
    conn.commit()
    conn.close()
    return JsonResponse({'success': True, 'message': 'Отзыв удалён'})


def avatar_serve_view(request, user_id):
    _ensure_avatar_column()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT avatar FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row or not row[0]:
        from django.http import HttpResponseNotFound
        return HttpResponseNotFound()
    path = os.path.join(AVATAR_DIR, row[0])
    if not os.path.exists(path):
        from django.http import HttpResponseNotFound
        return HttpResponseNotFound()
    from django.http import FileResponse
    return FileResponse(open(path, 'rb'))


@csrf_exempt
@require_http_methods(["POST"])
def api_upload_avatar(request):
    if not check_auth(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    user_id = request.session.get('user_id')
    _ensure_avatar_column()

    file = request.FILES.get('avatar')
    if not file:
        return JsonResponse({'success': False, 'error': 'Файл не выбран'})

    import imghdr
    valid_ext = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/gif': '.gif'}
    if file.content_type not in valid_ext:
        return JsonResponse({'success': False, 'error': 'Допустимы только JPG, PNG, WebP, GIF'})

    if file.size > 2 * 1024 * 1024:
        return JsonResponse({'success': False, 'error': 'Максимальный размер — 2MB'})

    os.makedirs(AVATAR_DIR, exist_ok=True)
    ext = valid_ext[file.content_type]
    filename = f'avatar_{user_id}_{int(time.time())}{ext}'
    filepath = os.path.join(AVATAR_DIR, filename)

    with open(filepath, 'wb+') as dest:
        for chunk in file.chunks():
            dest.write(chunk)

    conn = get_db()
    cur = conn.cursor()
    old = cur.execute("SELECT avatar FROM users WHERE user_id=?", (user_id,)).fetchone()
    if old and old[0]:
        old_path = os.path.join(AVATAR_DIR, old[0])
        if os.path.exists(old_path):
            os.remove(old_path)
    cur.execute("UPDATE users SET avatar=? WHERE user_id=?", (filename, user_id))
    conn.commit()
    conn.close()

    log_user_action(user_id, 'avatar_changed', 'Загружен новый аватар', request)
    return JsonResponse({'success': True, 'avatar_url': _avatar_url(user_id)})


# ===================== SECURITY AUDIT LOG =====================

ACTION_TYPE_LABELS = {
    'password_changed': 'Изменение пароля',
    'password_reset': 'Восстановление пароля',
    '2fa_enabled': 'Включение 2FA',
    '2fa_disabled': 'Отключение 2FA',
    'withdrawal_details_changed': 'Изменение реквизитов вывода',
    'profile_updated': 'Обновление профиля',
    'avatar_changed': 'Смена аватара',
}


def security_log_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')
    user_id = request.session.get('user_id')
    page = int(request.GET.get('page', 1))
    per_page = 30
    offset = (page - 1) * per_page
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM user_audit_log WHERE user_id=?", (user_id,))
    total = cur.fetchone()[0]
    cur.execute(
        "SELECT id, action_type, details, ip_address, user_agent, created_at "
        "FROM user_audit_log WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (user_id, per_page, offset)
    )
    logs = [dict(r) for r in cur.fetchall()]
    for log in logs:
        log['action_label'] = ACTION_TYPE_LABELS.get(log['action_type'], log['action_type'])
    conn.close()
    return render(request, 'usersite/security_log.html', {
        'logs': logs,
        'page': page,
        'total_pages': (total + per_page - 1) // per_page,
    })


# ===================== EMAIL RECOVERY =====================

import secrets as _secrets
from datetime import datetime as _dt, timedelta as _td
from django.core.mail import send_mail as django_send_mail


def password_reset_request_view(request):
    return render(request, 'usersite/password_reset_request.html')


@csrf_exempt
@require_http_methods(["POST"])
def api_password_reset_request(request):
    data = json.loads(request.body)
    email = data.get('email', '').strip().lower()
    if not email or not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
        return JsonResponse({'success': False, 'error': 'Некорректный email'}, status=400)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id, username FROM users WHERE LOWER(profile_email)=? AND profile_setup_complete=1", (email,))
    user = cur.fetchone()
    if not user:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Пользователь с таким email не найден'}, status=404)
    user_id, username = user
    token = _secrets.token_urlsafe(32)
    expires = (_dt.now() + _td(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    cur.execute(
        "INSERT INTO password_reset_tokens (user_id, token, email, expires_at) VALUES (?, ?, ?, ?)",
        (user_id, token, email, expires)
    )
    conn.commit()
    conn.close()
    bot_name = getattr(settings, 'TELEGRAM_BOT_USERNAME', 'NovixGiftBot')
    reset_link = f"{request.scheme}://{request.get_host()}/usersite/password-reset/{token}/"
    try:
        django_send_mail(
            subject='Восстановление пароля — Heyken',
            message=f'Здравствуйте!\n\nВы запросили восстановление пароля.\n\n'
                    f'Перейдите по ссылке для сброса пароля:\n{reset_link}\n\n'
                    f'Ссылка действительна 1 час.\n\nЕсли вы не запрашивали сброс, проигнорируйте это письмо.',
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@heyken.io'),
            recipient_list=[email],
            fail_silently=False,
        )
        return JsonResponse({'success': True, 'message': 'Письмо отправлено на ваш email'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Ошибка отправки: {str(e)}. Проверьте настройки SMTP.'}, status=500)


def password_reset_confirm_view(request, token):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, email, expires_at, used FROM password_reset_tokens WHERE token=?",
        (token,)
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return render(request, 'usersite/password_reset_confirm.html', {'error': 'Недействительный токен'})
    user_id, email, expires_at, used = row
    if used:
        return render(request, 'usersite/password_reset_confirm.html', {'error': 'Токен уже использован'})
    try:
        expires = _dt.fromisoformat(expires_at.replace('Z', ''))
        if expires < _dt.now():
            return render(request, 'usersite/password_reset_confirm.html', {'error': 'Срок действия токена истёк'})
    except:
        pass
    return render(request, 'usersite/password_reset_confirm.html', {
        'token': token,
        'email': email,
    })


@csrf_exempt
@require_http_methods(["POST"])
def api_password_reset_confirm(request):
    data = json.loads(request.body)
    token = data.get('token', '').strip()
    password = data.get('password', '')
    if not token or len(password) < 6:
        return JsonResponse({'success': False, 'error': 'Пароль минимум 6 символов'}, status=400)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, email, expires_at, used FROM password_reset_tokens WHERE token=?",
        (token,)
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Токен не найден'}, status=404)
    user_id, email, expires_at, used = row
    if used:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Токен уже использован'}, status=400)
    try:
        expires = _dt.fromisoformat(expires_at.replace('Z', ''))
        if expires < _dt.now():
            conn.close()
            return JsonResponse({'success': False, 'error': 'Срок действия токена истёк'}, status=400)
    except:
        pass
    password_hash = make_password(password)
    cur.execute("UPDATE users SET profile_password_hash=? WHERE user_id=?", (password_hash, user_id))
    cur.execute("UPDATE password_reset_tokens SET used=1 WHERE token=?", (token,))
    conn.commit()
    conn.close()
    log_user_action(user_id, 'password_reset', f'Восстановление пароля через email: {email}', request)
    return JsonResponse({'success': True, 'message': 'Пароль успешно изменён'})


# ===================== P2P EXCHANGE =====================

@safe_db
def exchange_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')
    user_id = request.session.get('user_id')
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM exchange_offers WHERE status='active' ORDER BY created_at DESC LIMIT 50")
        offers = [dict(r) for r in cur.fetchall()]
    except Exception:
        offers = []
    try:
        cur.execute("SELECT * FROM exchange_offers WHERE user_id=? ORDER BY created_at DESC LIMIT 20", (user_id,))
        my_offers = [dict(r) for r in cur.fetchall()]
    except Exception:
        my_offers = []
    try:
        cur.execute("SELECT * FROM exchange_deals WHERE buyer_id=? OR seller_id=? ORDER BY created_at DESC LIMIT 20", (user_id, user_id))
        my_deals_ex = [dict(r) for r in cur.fetchall()]
    except Exception:
        my_deals_ex = []
    # User's tier & commission for display
    cur.execute("SELECT premium_tier FROM users WHERE user_id=?", (user_id,))
    tier_row = cur.fetchone()
    user_tier = tier_row[0] if tier_row else 'free'
    commission_rate = TIER_COMMISSION.get(user_tier, 4)
    conn.close()
    return render(request, 'usersite/exchange.html', {
        'offers': offers,
        'my_offers': my_offers,
        'my_deals': my_deals_ex,
        'currencies': ['RUB', 'USD', 'EUR', 'TON', 'USDT'],
        'exchange_rates': EXCHANGE_RATES,
        'user_tier': user_tier,
        'user_commission': commission_rate,
    })


@csrf_exempt
@safe_db
@require_http_methods(["POST"])
def api_exchange_create_offer(request):
    if not check_auth(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    user_id = request.session.get('user_id')
    data = json.loads(request.body)
    give_cur = data.get('give_currency', '').upper()
    give_amt = float(data.get('give_amount', 0))
    recv_cur = data.get('receive_currency', '').upper()
    recv_amt = float(data.get('receive_amount', 0))
    allowed = ['RUB', 'USD', 'EUR', 'TON', 'USDT']
    if give_cur not in allowed or recv_cur not in allowed:
        return JsonResponse({'error': 'Недопустимая валюты'}, status=400)
    if give_amt <= 0:
        return JsonResponse({'error': 'Сумма должна быть > 0'}, status=400)
    if give_cur == recv_cur:
        return JsonResponse({'error': 'Валюты должны различаться'}, status=400)
    # Auto-calculate receive amount if not provided
    if recv_amt <= 0:
        give_rate = EXCHANGE_RATES.get(give_cur, 1)
        recv_rate = EXCHANGE_RATES.get(recv_cur, 1)
        recv_amt = round(give_amt * give_rate / recv_rate, 2) if recv_rate else 0
        if recv_amt <= 0:
            return JsonResponse({'error': 'Не удалось рассчитать сумму'}, status=400)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT balance_{} FROM users WHERE user_id=?".format(give_cur), (user_id,))
    row = cur.fetchone()
    bal = row[0] or 0
    if bal < give_amt:
        conn.close()
        return JsonResponse({'error': 'Недостаточно средств'}, status=400)
    cur.execute(
        "INSERT INTO exchange_offers (user_id, give_currency, give_amount, receive_currency, receive_amount) VALUES (?, ?, ?, ?, ?)",
        (user_id, give_cur, give_amt, recv_cur, recv_amt)
    )
    conn.commit()
    conn.close()
    return JsonResponse({'success': True})


@csrf_exempt
@safe_db
@require_http_methods(["POST"])
def api_exchange_accept(request):
    if not check_auth(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    buyer_id = request.session.get('user_id')
    data = json.loads(request.body)
    offer_id = int(data.get('offer_id', 0))
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM exchange_offers WHERE id=? AND status='active'", (offer_id,))
    offer = cur.fetchone()
    if not offer:
        conn.close()
        return JsonResponse({'error': 'Ордер не найден или уже неактивен'}, status=404)
    offer = dict(offer)
    if offer['user_id'] == buyer_id:
        conn.close()
        return JsonResponse({'error': 'Нельзя принять свой ордер'}, status=400)
    seller_id = offer['user_id']
    give_cur, give_amt = offer['give_currency'], offer['give_amount']
    recv_cur, recv_amt = offer['receive_currency'], offer['receive_amount']
    cur.execute("SELECT balance_{} FROM users WHERE user_id=?".format(recv_cur), (buyer_id,))
    row = cur.fetchone()
    if (row[0] or 0) < recv_amt:
        conn.close()
        return JsonResponse({'error': 'Недостаточно средств для обмена'}, status=400)
    # Commission based on buyer's subscription tier
    cur.execute("SELECT premium_tier FROM users WHERE user_id=?", (buyer_id,))
    tier_row = cur.fetchone()
    tier = tier_row[0] if tier_row else 'free'
    commission_rate = TIER_COMMISSION.get(tier, 4) / 100
    commission = round(recv_amt * commission_rate, 2)
    seller_gets = recv_amt - commission
    cur.execute("BEGIN IMMEDIATE")
    try:
        cur.execute("UPDATE exchange_offers SET status='completed' WHERE id=?", (offer_id,))
        cur.execute("UPDATE users SET balance_{}=balance_{}-{} WHERE user_id=?".format(give_cur, give_cur, give_amt, seller_id) if False else
                    "UPDATE users SET balance_{}=balance_{}-? WHERE user_id=?".format(give_cur, give_cur), (give_amt, seller_id))
        cur.execute("UPDATE users SET balance_{}=balance_{}+? WHERE user_id=?".format(give_cur, give_cur), (give_amt, buyer_id))
        cur.execute("UPDATE users SET balance_{}=balance_{}-? WHERE user_id=?".format(recv_cur, recv_cur), (recv_amt, buyer_id))
        cur.execute("UPDATE users SET balance_{}=balance_{}+? WHERE user_id=?".format(recv_cur, recv_cur), (seller_gets, seller_id))
        cur.execute(
            "INSERT INTO exchange_deals (offer_id, buyer_id, seller_id, give_currency, give_amount, receive_currency, receive_amount, commission, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'completed')",
            (offer_id, buyer_id, seller_id, give_cur, give_amt, recv_cur, seller_gets, commission)
        )
        conn.commit()
        return JsonResponse({'success': True, 'message': 'Обмен выполнен'})
    except Exception as e:
        conn.rollback()
        return JsonResponse({'error': str(e)}, status=500)
    finally:
        conn.close()


@csrf_exempt
@safe_db
@require_http_methods(["POST"])
def api_exchange_cancel(request):
    if not check_auth(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    user_id = request.session.get('user_id')
    data = json.loads(request.body)
    offer_id = int(data.get('offer_id', 0))
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE exchange_offers SET status='cancelled' WHERE id=? AND user_id=? AND status='active'", (offer_id, user_id))
    conn.commit()
    conn.close()
    return JsonResponse({'success': True})


def verification_view(request):
    _ensure_verification_columns()
    ctx = {}
    if check_auth(request):
        conn = get_db()
        try:
            cur = conn.cursor()
            user_id = request.session.get('user_id')
            cur.execute("SELECT is_verified_partner, verified_reason, verified_at FROM users WHERE user_id=?", (user_id,))
            row = cur.fetchone()
            if row:
                ctx['is_verified'] = row[0]
                ctx['verified_reason'] = row[1]
                ctx['verified_at_dt'] = row[2]
            cur.execute("SELECT COUNT(*) FROM verification_applications WHERE user_id=?", (user_id,))
            ctx['my_applications_count'] = cur.fetchone()[0] or 0
        finally:
            conn.close()
    return render(request, 'usersite/verification.html', ctx)


VERIFICATION_CATEGORIES = [
    ('community', 'Вклад в сообщество', 'Активное участие в жизни платформы, помощь другим пользователям'),
    ('partner', 'Партнёрская деятельность', 'Привлечение новых пользователей, развитие бренда'),
    ('expertise', 'Экспертиза и опыт', 'Подтверждённый опыт в сделках, уникальные навыки'),
    ('purchase', 'Покупка ($5,000/год)', 'Оплатить верификацию на 1 год — $5000'),
]


def verification_terms_view(request):
    return render(request, 'usersite/verification_terms.html')


def verification_apply_view(request):
    _ensure_verification_columns()
    if not check_auth(request):
        return redirect('/usersite/login/')
    conn = get_db()
    try:
        cur = conn.cursor()
        user_id = request.session.get('user_id')
        cur.execute("SELECT is_verified_partner FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        already = row and row[0]
    finally:
        conn.close()
    return render(request, 'usersite/verification_apply.html', {
        'categories': VERIFICATION_CATEGORIES,
        'already_verified': already,
    })


def verification_requests_view(request):
    _ensure_verification_columns()
    if not check_auth(request):
        return redirect('/usersite/login/')
    conn = get_db()
    try:
        cur = conn.cursor()
        user_id = request.session.get('user_id')
        cur.execute(
            "SELECT id, category, description, status, admin_reason, created_at, updated_at "
            "FROM verification_applications WHERE user_id=? ORDER BY created_at DESC",
            (user_id,)
        )
        applications = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return render(request, 'usersite/verification_requests.html', {
        'applications': applications,
    })


@csrf_exempt
@require_http_methods(["POST"])
def verification_apply_api(request):
    if not check_auth(request):
        return JsonResponse({'success': False, 'error': 'Not logged in'}, status=401)
    user_id = request.session.get('user_id')
    category = request.POST.get('category', '')
    description = request.POST.get('description', '')

    valid_cats = [c[0] for c in VERIFICATION_CATEGORIES]
    if category not in valid_cats:
        return JsonResponse({'success': False, 'error': 'Некорректная категория'})
    if not description:
        return JsonResponse({'success': False, 'error': 'Опишите причину заявки'})

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_verified_partner FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if row and row[0]:
            return JsonResponse({'success': False, 'error': 'Вы уже верифицированы'})
        cur.execute(
            "INSERT INTO verification_applications (user_id, category, description) VALUES (?,?,?)",
            (user_id, category, description)
        )
        conn.commit()
    finally:
        conn.close()
    return JsonResponse({'success': True})


@csrf_exempt
@require_http_methods(["POST"])
def verification_purchase_api(request):
    if not check_auth(request):
        return JsonResponse({'success': False, 'error': 'Not logged in'}, status=401)
    user_id = request.session.get('user_id')
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_verified_partner FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if row and row[0]:
            return JsonResponse({'success': False, 'error': 'Вы уже верифицированы'})
        balance = 0
        cur.execute("SELECT balance_USDT FROM users WHERE user_id=?", (user_id,))
        r = cur.fetchone()
        if r:
            balance = float(r[0] or 0)
        if balance < 5000:
            return JsonResponse({'success': False, 'error': f'Недостаточно USDT. Нужно 5000 USDT, у вас {balance:.2f} USDT'})
        cur.execute("UPDATE users SET balance_USDT = balance_USDT - 5000 WHERE user_id=?", (user_id,))
        cur.execute(
            "UPDATE users SET is_verified_partner = 1, verified_at = CURRENT_TIMESTAMP, "
            "verified_by = NULL, verified_reason = 'Purchased ($5000/year)' WHERE user_id=?",
            (user_id,)
        )
        cur.execute(
            "INSERT INTO verification_history (user_id, action, reason, admin_id) VALUES (?, 'granted', 'Purchased ($5000/year)', 0)",
            (user_id,)
        )
        conn.commit()
    finally:
        conn.close()
    return JsonResponse({'success': True})


def terms_view(request):
    return render(request, 'usersite/terms.html')


def privacy_view(request):
    return render(request, 'usersite/privacy.html')


@safe_db
def create_deal_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')
    user_id = request.session.get('user_id')
    bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', 'NovixGiftBot')

    if request.method == 'POST':
        item_name = (request.POST.get('item_name') or '').strip()
        price_str = (request.POST.get('price') or '').strip()
        currency = (request.POST.get('currency') or 'RUB').strip().upper()
        is_public = request.POST.get('is_public') == '1'

        allowed = ['RUB', 'BYN', 'UAH', 'KZT', 'UZS', 'EUR', 'USD', 'TON', 'USDT', 'STARS']
        if currency not in allowed:
            currency = 'RUB'

        errors = []
        if not item_name:
            errors.append('Введите название товара')
        if len(item_name) > 200:
            errors.append('Название товара слишком длинное (макс. 200 символов)')

        try:
            price = float(price_str)
            if price <= 0:
                errors.append('Цена должна быть больше 0')
        except (ValueError, TypeError):
            errors.append('Введите корректную цену')

        if errors:
            return render(request, 'usersite/create_deal.html', {
                'errors': errors,
                'item_name': item_name,
                'price': price_str,
                'currency': currency,
                'currencies': allowed,
                'is_public': is_public,
                'bot_username': bot_username,
            })

        conn = get_db()
        cur = conn.cursor()

        deal_code = generate_deal_code(cur)

        commission = 0.04
        cur.execute("SELECT premium_tier FROM users WHERE user_id=?", (user_id,))
        urow = cur.fetchone()
        if urow:
            tier = urow[0] or 'free'
            tier_commissions = {'free': 0.04, 'premium': 0.02, 'platinum': 0.01, 'vip': 0.0}
            commission = tier_commissions.get(tier, 0.04)

        initial_status = 'open' if is_public else 'awaiting'
        cur.execute("""
            INSERT INTO deals (seller, item, amount, commission, currency, status, deal_code, is_public)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, item_name, price, commission, currency, initial_status, deal_code, 1 if is_public else 0))
        conn.commit()
        deal_id = cur.lastrowid
        conn.close()

        if is_public:
            return redirect('/usersite/marketplace/')

        return redirect(f'/usersite/deal/success/?code={deal_code}&id={deal_id}')

    return render(request, 'usersite/create_deal.html', {
        'currencies': ['RUB', 'BYN', 'UAH', 'KZT', 'UZS', 'EUR', 'USD', 'TON', 'USDT', 'STARS'],
        'bot_username': bot_username,
    })


def deal_success_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')
    deal_code = request.GET.get('code', '')
    deal_id = request.GET.get('id', '')
    bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', 'NovixGiftBot')
    tg_link = f"https://t.me/{bot_username}?start=deal_{deal_code}"
    display_code = f"#{deal_code}"
    return render(request, 'usersite/deal_success.html', {
        'deal_code': deal_code,
        'display_code': display_code,
        'deal_id': deal_id,
        'tg_link': tg_link,
        'bot_username': bot_username,
    })


# ========== DEAL DETAIL + PAYMENT PAGES (Usersite) ==========

def _call_bot_internal(endpoint: str, user_id: int, extra: dict = None) -> dict:
    """Server-to-server вызов к bot.py. user_id всегда из сессии, не из тела запроса."""
    body = {'user_id': user_id}
    if extra:
        body.update(extra)
    try:
        r = requests.post(
            f"{BACKEND_URL}{endpoint}",
            json=body,
            headers={"X-Internal-Secret": INTERNAL_API_SECRET},
            timeout=10
        )
        return r.json()
    except Exception as e:
        return {'success': False, 'error': f'Bot unavailable: {e}'}


@safe_db
def deal_detail_view(request, deal_id):
    if not check_auth(request):
        return redirect('/usersite/login/')
    user_id = request.session.get('user_id')

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM deals WHERE id=? AND (buyer=? OR seller=?)", (deal_id, user_id, user_id))
    deal = cur.fetchone()

    if not deal:
        conn.close()
        return redirect('/usersite/profile/')

    deal_dict = dict(deal)
    is_buyer = deal_dict['buyer'] == user_id
    is_seller = deal_dict['seller'] == user_id
    status = deal_dict['status']

    available_actions = []
    if is_buyer and status == 'awaiting':
        available_actions.append('pay')
    if is_seller and status == 'paid':
        available_actions.append('mark_sent')
    if is_buyer and status == 'item_sent':
        available_actions.append('confirm_receipt')

    cur.execute("SELECT * FROM deal_messages WHERE deal_id=? ORDER BY created_at ASC", (deal_id,))
    messages = [dict(r) for r in cur.fetchall()]
    conn.close()

    return render(request, 'usersite/deal_detail.html', {
        'deal': deal_dict,
        'is_buyer': is_buyer,
        'is_seller': is_seller,
        'available_actions': available_actions,
        'messages': messages,
        'user_id': user_id,
        'bot_username': getattr(settings, 'TELEGRAM_BOT_USERNAME', 'NovixGiftBot'),
        'counter_offers': deal_dict.get('counter_offers') or 0,
        'proposed_amount': deal_dict.get('proposed_amount'),
        'proposed_by': deal_dict.get('proposed_by'),
    })


@safe_db
def deal_pay_view(request, deal_id):
    if not check_auth(request):
        return redirect('/usersite/login/')
    user_id = request.session.get('user_id')

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM deals WHERE id=? AND buyer=?", (deal_id, user_id))
    deal = cur.fetchone()

    if not deal:
        conn.close()
        return redirect('/usersite/profile/')

    deal_dict = dict(deal)
    if deal_dict['status'] not in ('awaiting',):
        conn.close()
        return redirect(f'/usersite/deal/{deal_id}/')

    # Commission breakdown — volume-based fee
    price = float(deal_dict['amount'])
    commission_rate = float(_get_fee_rate(deal_dict['seller']))
    commission_amount = round(price * commission_rate, 2)
    seller_gets = price - commission_amount

    # Rate lock
    locked_rate = deal_dict.get('locked_rate')
    rate_expires_at = deal_dict.get('rate_expires_at')
    if locked_rate is None:
        try:
            import sys as _sys
            _sys.path.insert(0, os.path.join(BASE_DIR, '..'))
            from currency_api import currency_api as _ca
            rates = _ca.get_stale_cache("RUB")
            locked_rate = rates.get('RUB', 1.0)
            import datetime as _dt
            expires = (_dt.datetime.now() + _dt.timedelta(hours=24)).isoformat()
            cur.execute(
                "UPDATE deals SET locked_rate=?, rate_expires_at=? WHERE id=?",
                (locked_rate, expires, deal_id)
            )
            conn.commit()
            rate_expires_at = expires
        except Exception:
            locked_rate = 1.0

    conn.close()

    volume_info = _get_vol_tier_info(deal_dict['seller'])

    return render(request, 'usersite/deal_pay.html', {
        'deal': deal_dict,
        'commission_rate': int(commission_rate * 100),
        'commission_amount': commission_amount,
        'seller_gets': seller_gets,
        'locked_rate': locked_rate,
        'rate_expires_at': rate_expires_at,
        'bot_username': getattr(settings, 'TELEGRAM_BOT_USERNAME', 'NovixGiftBot'),
        'volume_tier_info': volume_info,
    })


@require_http_methods(["POST"])
def api_deal_pay(request, deal_id):
    if not check_auth(request):
        return JsonResponse({'success': False, 'error': 'Unauthorized'}, status_code=401)
    user_id = request.session.get('user_id')
    result = _call_bot_internal('/api/pay_deal', user_id, {'deal_id': deal_id})
    return JsonResponse(result)


@require_http_methods(["POST"])
def api_deal_mark_sent(request, deal_id):
    if not check_auth(request):
        return JsonResponse({'success': False, 'error': 'Unauthorized'}, status_code=401)
    user_id = request.session.get('user_id')
    result = _call_bot_internal('/api/mark_sent', user_id, {'deal_id': deal_id})
    return JsonResponse(result)


@require_http_methods(["POST"])
def api_deal_confirm_receipt(request, deal_id):
    if not check_auth(request):
        return JsonResponse({'success': False, 'error': 'Unauthorized'}, status_code=401)
    user_id = request.session.get('user_id')
    result = _call_bot_internal('/api/confirm_receipt', user_id, {'deal_id': deal_id})
    return JsonResponse(result)


@require_http_methods(["POST"])
def api_deal_propose_amount(request, deal_id):
    if not check_auth(request):
        return JsonResponse({'success': False, 'error': 'Unauthorized'}, status_code=401)
    user_id = request.session.get('user_id')
    try:
        data = json.loads(request.body)
        proposed = float(data.get('amount', 0))
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({'success': False, 'error': 'Некорректная сумма'}, status_code=400)
    if proposed <= 0:
        return JsonResponse({'success': False, 'error': 'Сумма должна быть больше 0'}, status_code=400)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM deals WHERE id=?", (deal_id,))
    deal = cur.fetchone()
    if not deal:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Сделка не найдена'}, status_code=404)
    deal_dict = dict(deal)
    conn.close()
    if deal_dict['status'] != 'awaiting':
        return JsonResponse({'success': False, 'error': 'Сделка уже оплачена или закрыта'})
    if deal_dict['seller'] == user_id:
        return JsonResponse({'success': False, 'error': 'Вы не можете предлагать сумму в своей сделке'})
    if deal_dict.get('buyer') and deal_dict['buyer'] != user_id:
        return JsonResponse({'success': False, 'error': 'Сделка закреплена за другим покупателем'})
    if (deal_dict.get('counter_offers') or 0) >= 3:
        return JsonResponse({'success': False, 'error': 'Лимит предложений (3) исчерпан'})
    result = _call_bot_internal('/api/internal/propose-amount', user_id, {
        'deal_id': deal_id,
        'proposed_amount': proposed,
    })
    return JsonResponse(result)


@require_http_methods(["GET"])
def api_user_fee_rate(request):
    if not check_auth(request):
        return JsonResponse({'success': False, 'error': 'Unauthorized'}, status_code=401)
    user_id = request.session.get('user_id')
    info = _get_vol_tier_info(user_id)
    return JsonResponse({'success': True, **info})


# ========== DEAL CHAT (сообщения и вложения) ==========

@require_http_methods(["POST"])
def api_deal_send_message(request, deal_id):
    if not check_auth(request):
        return JsonResponse({'success': False, 'error': 'Unauthorized'}, status_code=401)
    user_id = request.session.get('user_id')
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM deals WHERE id=? AND (buyer=? OR seller=?)", (deal_id, user_id, user_id))
    deal = cur.fetchone()
    if not deal:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Сделка не найдена'}, status_code=404)
    text = request.POST.get('message', '').strip()
    f = request.FILES.get('attachment')
    attachment_path = None
    if f:
        ext = os.path.splitext(f.name)[1]
        safe_name = f"{deal_id}_{int(time.time())}_{random.randint(1000,9999)}{ext}"
        dest = os.path.join(DEAL_ATTACHMENTS_DIR, safe_name)
        with open(dest, 'wb') as out:
            for chunk in f.chunks():
                out.write(chunk)
        attachment_path = safe_name
    cur.execute(
        "INSERT INTO deal_messages (deal_id, sender_id, message, attachment_path) VALUES (?, ?, ?, ?)",
        (deal_id, user_id, text or None, attachment_path)
    )
    conn.commit()
    conn.close()
    return JsonResponse({'success': True})


@require_http_methods(["GET"])
def api_deal_messages(request, deal_id):
    if not check_auth(request):
        return JsonResponse({'success': False, 'error': 'Unauthorized'}, status_code=401)
    user_id = request.session.get('user_id')
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM deals WHERE id=? AND (buyer=? OR seller=?)", (deal_id, user_id, user_id))
    if not cur.fetchone():
        conn.close()
        return JsonResponse({'success': False, 'error': 'Сделка не найдена'}, status_code=404)
    cur.execute("SELECT * FROM deal_messages WHERE deal_id=? ORDER BY created_at ASC", (deal_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return JsonResponse({'success': True, 'messages': rows})


@require_http_methods(["GET"])
def deal_attachment_serve(request, deal_id, filename):
    if not check_auth(request):
        return redirect('/usersite/login/')
    user_id = request.session.get('user_id')
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM deals WHERE id=? AND (buyer=? OR seller=?)", (deal_id, user_id, user_id))
    if not cur.fetchone():
        conn.close()
        return redirect('/usersite/profile/')
    conn.close()
    filepath = os.path.join(DEAL_ATTACHMENTS_DIR, filename)
    if not os.path.isfile(filepath):
        return JsonResponse({'error': 'Файл не найден'}, status_code=404)
    from django.http import FileResponse
    return FileResponse(open(filepath, 'rb'), filename=filename)


# ========== MARKETPLACE (витрина открытых сделок) ==========

CURRENCY_SYMBOLS_MAP = {'RUB': '₽', 'USD': '$', 'EUR': '€', 'BYN': 'Br', 'UAH': '₴', 'KZT': '₸', 'UZS': "so'm", 'TON': 'TON', 'USDT': 'USDT', 'STARS': '★'}

def _build_marketplace_query(params: dict) -> tuple:
    where = "d.status='open' AND d.is_public=1"
    bind = []
    if params.get('currency') and params['currency'] in CURRENCIES:
        where += " AND d.currency=?"
        bind.append(params['currency'])
    if params.get('min_amount'):
        try:
            where += " AND d.amount>=?"
            bind.append(float(params['min_amount']))
        except (ValueError, TypeError):
            pass
    if params.get('max_amount'):
        try:
            where += " AND d.amount<=?"
            bind.append(float(params['max_amount']))
        except (ValueError, TypeError):
            pass
    min_rating = None
    if params.get('min_rating'):
        try:
            min_rating = float(params['min_rating'])
        except (ValueError, TypeError):
            pass
    return where, bind, min_rating


@safe_db
def marketplace_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')
    user_id = request.session.get('user_id')

    page = request.GET.get('page', 1)
    try:
        page = int(page)
        if page < 1:
            page = 1
    except (ValueError, TypeError):
        page = 1
    per_page = 20

    where, bind, min_rating = _build_marketplace_query(request.GET)

    count_sql = f"SELECT COUNT(*) FROM deals d WHERE {where}"
    deals_sql = (
        f"SELECT d.*, u.username, "
        f"(SELECT COALESCE(AVG(rating), 0) FROM reviews WHERE reviewed_id=d.seller) as seller_rating, "
        f"(SELECT COUNT(*) FROM reviews WHERE reviewed_id=d.seller) as seller_reviews_count "
        f"FROM deals d LEFT JOIN users u ON u.user_id=d.seller "
        f"WHERE {where} ORDER BY d.created DESC"
    )

    conn = get_db()
    cur = conn.cursor()

    cur.execute(count_sql, bind)
    total = cur.fetchone()[0] or 0

    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page
    cur.execute(f"{deals_sql} LIMIT ? OFFSET ?", bind + [per_page, offset])
    deals = []
    for row in cur.fetchall():
        d = dict(row)
        if min_rating is not None and d['seller_rating'] < min_rating:
            continue
        d['seller_name'] = d.get('username') or f"ID{d['seller']}"
        d['symbol'] = CURRENCY_SYMBOLS_MAP.get(d['currency'], d['currency'])
        deals.append(d)

    conn.close()

    # Re-count after rating filter
    if min_rating is not None and deals:
        actual_total = total
        actual_pages = total_pages
    else:
        actual_total = total
        actual_pages = total_pages

    query_params = request.GET.copy()
    if 'page' in query_params:
        del query_params['page']
    qs = query_params.urlencode()

    # Build page range for pagination display
    page_range = []
    if actual_pages <= 7:
        page_range = list(range(1, actual_pages + 1))
    else:
        page_range = [1]
        if page > 3:
            page_range.append('...')
        start = max(2, page - 1)
        end = min(actual_pages - 1, page + 1)
        for p in range(start, end + 1):
            page_range.append(p)
        if page < actual_pages - 2:
            page_range.append('...')
        page_range.append(actual_pages)

    return render(request, 'usersite/marketplace.html', {
        'deals': deals,
        'page': page,
        'total_pages': actual_pages,
        'total': actual_total,
        'page_range': page_range,
        'qs': qs,
        'currencies': CURRENCIES,
        'symbols': CURRENCY_SYMBOLS_MAP,
        'filters': {
            'currency': request.GET.get('currency', ''),
            'min_amount': request.GET.get('min_amount', ''),
            'max_amount': request.GET.get('max_amount', ''),
            'min_rating': request.GET.get('min_rating', ''),
        },
        'bot_username': getattr(settings, 'TELEGRAM_BOT_USERNAME', 'NovixGiftBot'),
    })


def api_claim_deal(request, deal_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status_code=405)
    if not check_auth(request):
        return JsonResponse({'success': False, 'error': 'Unauthorized'}, status_code=401)
    user_id = request.session.get('user_id')

    conn = get_db()
    cur = conn.cursor()

    # Atomic: захват сделки — только если статус 'open' (никто ещё не откликнулся)
    cur.execute(
        "UPDATE deals SET status='awaiting', buyer=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='open'",
        (user_id, deal_id)
    )
    conn.commit()
    rowcount = cur.rowcount
    conn.close()

    if rowcount == 0:
        return JsonResponse({
            'success': False,
            'error': 'Сделка уже занята другим покупателем',
        })

    return JsonResponse({
        'success': True,
        'redirect': f'/usersite/deal/{deal_id}/',
    })


# ========== NOTIFICATIONS ==========

def notifications_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')
    user_id = request.session.get('user_id')
    page = int(request.GET.get('page', 1))
    per_page = 20
    offset = (page - 1) * per_page
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM usersite_notifications WHERE user_id=?", (user_id,))
    total = cur.fetchone()[0]
    cur.execute(
        "SELECT id, type, title, body, link, is_read, created_at FROM usersite_notifications "
        "WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (user_id, per_page, offset)
    )
    notifs = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT COUNT(*) FROM usersite_notifications WHERE user_id=? AND is_read=0", (user_id,))
    unread_count = cur.fetchone()[0]
    conn.close()
    return render(request, 'usersite/notifications.html', {
        'notifications': notifs,
        'unread_count': unread_count,
        'page': page,
        'total_pages': (total + per_page - 1) // per_page,
    })


def notification_open_view(request, notif_id):
    if not check_auth(request):
        return redirect('/usersite/login/')
    user_id = request.session.get('user_id')
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE usersite_notifications SET is_read=1 WHERE id=? AND user_id=?",
        (notif_id, user_id)
    )
    conn.commit()
    cur.execute("SELECT link FROM usersite_notifications WHERE id=? AND user_id=?", (notif_id, user_id))
    row = cur.fetchone()
    conn.close()
    if row and row[0]:
        return redirect(row[0])
    return redirect('/usersite/notifications/')


@csrf_exempt
def api_notifications_unread_count(request):
    if not check_auth(request):
        return JsonResponse({'count': 0})
    user_id = request.session.get('user_id')
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM usersite_notifications WHERE user_id=? AND is_read=0", (user_id,))
    count = cur.fetchone()[0]
    conn.close()
    return JsonResponse({'count': count})


NOTIF_TYPES = ['promo_activated', 'deal_paid', 'review_received', 'deal_completed', 'achievement', 'premium_expiring', 'referral_bonus']


@csrf_exempt
def api_notification_prefs(request):
    if not check_auth(request):
        return JsonResponse({'success': False, 'error': 'Не авторизован'}, status=401)
    user_id = request.session.get('user_id')
    if request.method == 'GET':
        conn = get_db()
        cur = conn.cursor()
        prefs = {}
        for nt in NOTIF_TYPES:
            cur.execute(
                "SELECT enabled FROM usersite_notification_prefs WHERE user_id=? AND type=?",
                (user_id, nt)
            )
            row = cur.fetchone()
            prefs[nt] = row[0] if row else 1
        conn.close()
        return JsonResponse({'success': True, 'prefs': prefs})
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
    updated = data.get('prefs', {})
    conn = get_db()
    cur = conn.cursor()
    for ntype, enabled in updated.items():
        if ntype not in NOTIF_TYPES:
            continue
        cur.execute(
            "INSERT INTO usersite_notification_prefs (user_id, type, enabled) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, type) DO UPDATE SET enabled=?",
            (user_id, ntype, 1 if enabled else 0, 1 if enabled else 0)
        )
    conn.commit()
    conn.close()
    return JsonResponse({'success': True})


# ========== SESSIONS / DEVICES MANAGEMENT ==========

def sessions_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')
    _ensure_known_devices_table()
    user_id = request.session.get('user_id')
    session_key = request.session.session_key or ''
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM known_devices WHERE user_id=? ORDER BY last_seen DESC",
        (user_id,)
    )
    devices = [dict(r) for r in cur.fetchall()]
    conn.close()
    return render(request, 'usersite/sessions.html', {
        'devices': devices,
        'current_session_key': session_key,
    })


@require_http_methods(["POST"])
def api_end_session(request, device_id):
    if not check_auth(request):
        return JsonResponse({'success': False, 'error': 'Unauthorized'}, status=401)
    _ensure_known_devices_table()
    user_id = request.session.get('user_id')
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM known_devices WHERE id=? AND user_id=?",
        (device_id, user_id)
    )
    device = cur.fetchone()
    if not device:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Устройство не найдено'}, status=404)
    d = dict(device)
    target_session = d.get('session_key') or None
    conn.close()
    if target_session:
        from django.contrib.sessions.models import Session
        Session.objects.filter(session_key=target_session).delete()
    return JsonResponse({'success': True})


@require_http_methods(["POST"])
def api_end_all_sessions(request):
    if not check_auth(request):
        return JsonResponse({'success': False, 'error': 'Unauthorized'}, status=401)
    _ensure_known_devices_table()
    user_id = request.session.get('user_id')
    current_key = request.session.session_key or ''
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT session_key FROM known_devices WHERE user_id=? AND session_key IS NOT NULL AND session_key != ?",
        (user_id, current_key)
    )
    keys = [r[0] for r in cur.fetchall() if r[0]]
    conn.close()
    from django.contrib.sessions.models import Session
    for k in keys:
        Session.objects.filter(session_key=k).delete()
    return JsonResponse({'success': True})


# ========== SERVICE STATUS (public /status/) ==========

def _is_admin_user(request) -> bool:
    role = request.session.get('admin_role')
    return role in ('CEO', 'Admin')


def status_view(request):
    """Публичная страница статуса сервиса (без авторизации)."""
    _ensure_incidents_table()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM incidents WHERE resolved_at IS NULL ORDER BY "
        "CASE status WHEN 'outage' THEN 0 WHEN 'degraded' THEN 1 WHEN 'operational' THEN 2 END ASC, "
        "started_at DESC LIMIT 1"
    )
    current = cur.fetchone()
    if current:
        current = dict(current)
    cur.execute("SELECT * FROM incidents ORDER BY started_at DESC LIMIT 50")
    incidents = [dict(r) for r in cur.fetchall()]
    conn.close()
    return render(request, 'status.html', {
        'current': current,
        'incidents': incidents,
    })


@require_http_methods(["GET", "POST"])
def incidents_admin_view(request):
    """Админка — управление инцидентами (CEO/Admin)."""
    if not _is_admin_user(request):
        return redirect('/login/')
    _ensure_incidents_table()
    conn = get_db()
    cur = conn.cursor()
    if request.method == "POST":
        title = request.POST.get('title', '').strip()
        status = request.POST.get('status', 'outage')
        description = request.POST.get('description', '').strip()
        if title:
            cur.execute(
                "INSERT INTO incidents (title, status, description) VALUES (?, ?, ?)",
                (title, status, description)
            )
            conn.commit()
            return redirect('/incidents/')
    cur.execute("SELECT * FROM incidents ORDER BY started_at DESC")
    incidents = [dict(r) for r in cur.fetchall()]
    conn.close()
    return render(request, 'incidents_admin.html', {
        'incidents': incidents,
        'active_page': 'incidents',
        'admin_name': request.session.get('username', 'Admin'),
    })


@require_http_methods(["POST"])
def api_close_incident(request, incident_id):
    if not _is_admin_user(request):
        return JsonResponse({'success': False, 'error': 'Forbidden'}, status=403)
    _ensure_incidents_table()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE incidents SET resolved_at=CURRENT_TIMESTAMP WHERE id=? AND resolved_at IS NULL",
        (incident_id,)
    )
    conn.commit()
    conn.close()
    return JsonResponse({'success': True})


# ========== P2P TRANSFER ==========

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:9207")
INTERNAL_API_SECRET = os.getenv("INTERNAL_API_SECRET", "")
MIN_TRANSFER_AMOUNT = 1


def send_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')
    user_id = request.session.get('user_id')
    conn = get_db()
    cur = conn.cursor()
    balances = {}
    for c in CURRENCIES:
        cur.execute(f"SELECT balance_{c} FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        bal = row[0] if row and row[0] else 0
        balances[c] = round(bal, 4 if c in ('TON', 'USDT') else 2)
    conn.close()
    return render(request, 'usersite/send.html', {
        'balances': balances,
        'min_amount': MIN_TRANSFER_AMOUNT,
    })


@csrf_exempt
def api_send_preview(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)
    if not check_auth(request):
        return JsonResponse({'success': False, 'error': 'Unauthorized'}, status=401)
    user_id = request.session.get('user_id')
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
    username = data.get('username', '').strip().lstrip('@')
    if not username:
        return JsonResponse({'success': False, 'error': 'Укажите получателя'})
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id, username FROM users WHERE username=?", (username,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Пользователь с таким username не найден'})
    to_user_id = row[0]
    to_username = row[1]
    if to_user_id == user_id:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Нельзя перевести самому себе'})
    currency = data.get('currency', '').upper()
    if currency not in CURRENCIES:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Валюта не поддерживается'})
    amount = data.get('amount', 0)
    try:
        amount = Decimal(str(amount))
        if amount < Decimal(str(MIN_TRANSFER_AMOUNT)):
            raise ValueError
    except (ValueError, TypeError):
        conn.close()
        return JsonResponse({'success': False, 'error': f'Минимальная сумма: {MIN_TRANSFER_AMOUNT}'})
    cur.execute(f"SELECT balance_{currency} FROM users WHERE user_id=?", (user_id,))
    bal_row = cur.fetchone()
    balance = Decimal(str(bal_row[0])) if bal_row and bal_row[0] else Decimal('0')
    if balance < amount:
        conn.close()
        return JsonResponse({'success': False, 'error': f'Недостаточно средств. Баланс: {balance} {currency}'})
    conn.close()
    return JsonResponse({
        'success': True,
        'to_user_id': to_user_id,
        'to_username': to_username,
        'currency': currency,
        'amount': float(amount),
    })


@csrf_exempt
def api_send_confirm(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)
    if not check_auth(request):
        return JsonResponse({'success': False, 'error': 'Unauthorized'}, status=401)
    user_id = request.session.get('user_id')
    conn = get_db()
    cur = conn.cursor()
    try:
        data = json.loads(request.body)
    except Exception:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
    username = data.get('username', '').strip().lstrip('@')
    currency = data.get('currency', '').upper()
    amount = data.get('amount', 0)
    note = data.get('note', '').strip()[:200]
    if not username or currency not in CURRENCIES:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Missing fields'})
    try:
        amount = Decimal(str(amount))
        if amount < Decimal(str(MIN_TRANSFER_AMOUNT)):
            raise ValueError
    except (ValueError, TypeError):
        conn.close()
        return JsonResponse({'success': False, 'error': f'Минимальная сумма: {MIN_TRANSFER_AMOUNT}'})
    cur.execute("SELECT user_id, username FROM users WHERE username=?", (username,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Получатель не найден'})
    to_user_id = row[0]
    to_username = row[1]
    if to_user_id == user_id:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Нельзя перевести самому себе'})
    cur.execute(f"SELECT balance_{currency} FROM users WHERE user_id=?", (user_id,))
    bal_row = cur.fetchone()
    balance = Decimal(str(bal_row[0])) if bal_row and bal_row[0] else Decimal('0')
    if balance < amount:
        conn.close()
        return JsonResponse({'success': False, 'error': f'Недостаточно средств. Баланс: {round(balance, 2)} {currency}'})
    cur.execute("SELECT username FROM users WHERE user_id=?", (user_id,))
    from_row = cur.fetchone()
    from_username = from_row[0] if from_row else str(user_id)
    conn.close()
    try:
        resp = requests.post(
            f"{BACKEND_URL}/api/internal/2fa-request",
            json={
                'user_id': user_id,
                'action': 'p2p_transfer',
                'payload': {
                    'to_user_id': to_user_id,
                    'to_username': to_username,
                    'currency': currency,
                    'amount': float(amount),
                    'note': note,
                    'from_username': from_username,
                }
            },
            timeout=10
        )
        data = resp.json()
        if data.get('success'):
            return JsonResponse({'success': True, 'message': 'Код подтверждения отправлен в Telegram'})
        else:
            return JsonResponse({'success': False, 'error': data.get('error', 'Ошибка отправки подтверждения')})
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Бот недоступен: {str(e)}'})


@csrf_exempt
def api_save_payment_details(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)
    if not check_auth(request):
        return JsonResponse({'success': False, 'error': 'Unauthorized'}, status=401)
    user_id = request.session.get('user_id')
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
    card_number = data.get('card_number', '').replace(' ', '')
    ton_wallet = (data.get('ton_wallet') or '').strip()
    card_currency = data.get('card_currency', 'RUB')
    if not card_number and not ton_wallet:
        return JsonResponse({'success': False, 'error': 'Укажите карту или TON кошелёк'})
    if card_number and (not card_number.isdigit() or len(card_number) not in (16, 19)):
        return JsonResponse({'success': False, 'error': 'Неверный формат карты (16 или 19 цифр)'})
    if ton_wallet and not ton_wallet.startswith('UQ') and not ton_wallet.startswith('EQ'):
        return JsonResponse({'success': False, 'error': 'Неверный формат TON кошелька (начинается с UQ или EQ)'})
    details_parts = []
    if card_number:
        masked = card_number[:4] + '****' + card_number[-4:]
        details_parts.append(f'карта: {masked} ({card_currency})')
    if ton_wallet:
        masked = ton_wallet[:4] + '...' + ton_wallet[-4:]
        details_parts.append(f'TON: {masked}')
    try:
        log_user_action(user_id, 'withdrawal_details_changed', 'Запрошено изменение: ' + ', '.join(details_parts), request)
    except Exception:
        pass

    try:
        resp = requests.post(
            f"{BACKEND_URL}/api/internal/2fa-request",
            json={
                'user_id': user_id,
                'action': 'change_payment_details',
                'payload': {
                    'card_number': card_number,
                    'ton_wallet': ton_wallet,
                    'card_currency': card_currency,
                }
            },
            timeout=10
        )
        d = resp.json()
        if d.get('success'):
            return JsonResponse({'success': True, 'message': 'Код подтверждения отправлен в Telegram'})
        else:
            return JsonResponse({'success': False, 'error': d.get('error', 'Ошибка отправки подтверждения')})
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Бот недоступен: {str(e)}'})
