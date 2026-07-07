import json, os, sqlite3, hashlib, hmac, random, re, time
from datetime import datetime, timedelta
from functools import wraps
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, '..', 'novixgift.db')

import sys
sys.path.insert(0, os.path.join(BASE_DIR, '..'))
from users.crypto_utils import decrypt_value, is_encryption_enabled as _enc_enabled
from users.views import OWNER_TELEGRAM_ID
from id_generator import generate_deal_code


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

def _ensure_avatar_column():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT avatar FROM users LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE users ADD COLUMN avatar TEXT DEFAULT ''")
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

        if name:
            request.session['username'] = name
            request.session.modified = True
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


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

    return JsonResponse({'success': True, 'avatar_url': _avatar_url(user_id)})


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

        cur.execute("""
            INSERT INTO deals (seller, item, amount, commission, currency, status, deal_code)
            VALUES (?, ?, ?, ?, ?, 'awaiting', ?)
        """, (user_id, item_name, price, commission, currency, deal_code))
        conn.commit()
        deal_id = cur.lastrowid
        conn.close()

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
