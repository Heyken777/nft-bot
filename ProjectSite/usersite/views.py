import json, os, sqlite3, hashlib, hmac, random, time
from datetime import datetime
from functools import wraps
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, '..', 'novixgift.db')

import sys
sys.path.insert(0, os.path.join(BASE_DIR, '..'))
from users.crypto_utils import decrypt_value, is_encryption_enabled as _enc_enabled

OWNER_TELEGRAM_ID = 1803437347


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=40)
    conn.row_factory = sqlite3.Row
    return conn





def safe_db(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except sqlite3.Error as e:
            if len(args) > 0 and hasattr(args[0], 'META'):
                request = args[0]
                from django.shortcuts import render
                return render(request, 'errors/db_error.html', {'error': str(e)}, status=500)
            return JsonResponse({'success': False, 'error': 'Database error'}, status=500)
        except Exception as e:
            if len(args) > 0 and hasattr(args[0], 'META'):
                request = args[0]
                from django.shortcuts import render
                return render(request, 'errors/db_error.html', {'error': str(e)}, status=500)
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    return wrapper


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
            request.session['telegram_id'] = user['user_id']
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
            request.session['telegram_id'] = user['user_id']
            request.session['username'] = user.get('username', str(user['user_id']))
            request.session.set_expiry(86400 * 7)
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
        telegram_id = int(raw)
        cur.execute("SELECT user_id FROM users WHERE user_id=?", (telegram_id,))
    else:
        username = raw.lstrip('@').strip()
        cur.execute("SELECT user_id FROM users WHERE username=?", (username,))

    user = cur.fetchone()
    if not user:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Пользователь не найден'}, status=404)

    user_id = user['user_id']
    code = f"{random.randint(100000, 999999)}"
    cur.execute(
        "INSERT INTO auth_codes (user_id, code, expires_at) VALUES (?, ?, datetime('now', '+5 minutes'))",
        (user_id, code)
    )
    cur.execute(
        "INSERT INTO notifications (user_id, title, message) VALUES (?, 'Код авторизации', ?)",
        (user_id, f"🔐 Ваш код для входа на сайт: <b>{code}</b>\nДействителен 5 минут.\n\nВведите его на странице входа или отправьте боту команду /code")
    )
    conn.commit()
    conn.close()
    return JsonResponse({'success': True, 'message': 'Код отправлен'})


CURRENCIES = ['RUB', 'USD', 'EUR', 'BYN', 'UAH', 'KZT', 'UZS', 'TON', 'USDT', 'STARS']
CURRENCY_SYMBOLS = {'RUB': '₽', 'USD': '$', 'EUR': '€', 'BYN': 'Br', 'UAH': '₴', 'KZT': '₸', 'UZS': 'сум', 'TON': 'TON', 'USDT': 'USDT', 'STARS': '⭐'}
EXCHANGE_RATES = {'RUB': 1, 'USD': 90, 'EUR': 98, 'BYN': 29, 'UAH': 2.4, 'KZT': 0.19, 'UZS': 0.0073, 'TON': 280, 'USDT': 90, 'STARS': 0.02}
TIER_BADGES = {'free': '⬜ FREE', 'premium': '⭐ PREMIUM', 'platinum': '💎 PLATINUM', 'vip': '👑 VIP'}
TIER_COMMISSION = {'free': 4, 'premium': 2, 'platinum': 1, 'vip': 0}

@safe_db
def dashboard_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')

    user_id = request.session.get('user_id')
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = cur.fetchone()
    user_dict = dict(user) if user else {}

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

    # Все балансы
    balances = []
    total_rub = 0
    for c in CURRENCIES:
        b = float(user_dict.get(f'balance_{c}', 0))
        sym = CURRENCY_SYMBOLS.get(c, c)
        rate = EXCHANGE_RATES.get(c, 1)
        total_rub += b * rate
        balances.append({'currency': c, 'symbol': sym, 'amount': b, 'rub_value': b * rate})

    # Активные сделки
    cur.execute("SELECT COUNT(*) FROM deals WHERE (buyer=? OR seller=?) AND status='awaiting'", (user_id, user_id))
    active_deals = cur.fetchone()[0]

    # Тикеты
    cur.execute("SELECT COUNT(*) FROM support_tickets WHERE user_id=?", (user_id,))
    tickets_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM support_tickets WHERE user_id=? AND status='open'", (user_id,))
    open_tickets = cur.fetchone()[0]

    # Уведомления
    try:
        cur.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 5", (user_id,))
        notifications = cur.fetchall()
    except:
        notifications = []

    try:
        cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0", (user_id,))
        unread_notifications = cur.fetchone()[0]
    except:
        unread_notifications = 0

    # Топ-10 верифицированных продавцов
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
        'user': user_dict,
        'deals': [dict(d) for d in deals],
        'balances': balances,
        'total_rub': total_rub,
        'purchases': purchases,
        'sales': sales,
        'total_earned': total_earned,
        'referrals_count': referrals_count,
        'tier': tier,
        'tier_badge': tier_badge,
        'tier_commission': tier_commission,
        'tier_active': tier_active,
        'premium_until': premium_until,
        'active_deals': active_deals,
        'tickets_count': tickets_count,
        'open_tickets': open_tickets,
        'notifications': [dict(n) for n in notifications],
        'unread_notifications': unread_notifications,
        'top_sellers': top_sellers,
        'bot_username': bot_username,
        'now': datetime.now(),
    })


@safe_db
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

    cur.execute("SELECT r.*, d.item AS deal_item FROM reviews r LEFT JOIN deals d ON r.deal_id=d.id WHERE r.reviewed_id=? ORDER BY r.created_at DESC LIMIT 10", (user_id,))
    reviews = cur.fetchall()

    cur.execute("SELECT AVG(rating) FROM reviews WHERE reviewed_id=?", (user_id,))
    avg_rating = cur.fetchone()[0] or 0

    conn.close()

    tier = (user and (user['premium_tier'] or 'free')) or 'free'
    premium_until = user and user['premium_until']
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

    tier_badge = TIER_BADGES.get(tier, '⬜ FREE')
    tier_commission = TIER_COMMISSION.get(tier, 4)

    bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', 'NovixGiftBot')
    return render(request, 'usersite/profile.html', {
        'user': dict(user) if user else None,
        'deals': [dict(d) for d in deals],
        'referrals': [dict(r) for r in referrals],
        'reviews': [dict(r) for r in reviews],
        'avg_rating': round(avg_rating, 1),
        'tier': tier,
        'tier_badge': tier_badge,
        'tier_commission': tier_commission,
        'tier_active': tier_active,
        'days_left': days_left,
        'bot_username': bot_username,
        'now': datetime.now(),
    })


def logout_view(request):
    request.session.flush()
    return redirect('/usersite/login/')


def user_profile_redirect(request, user_id):
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

TIER_BADGES_MAP = {'premium': '⭐ PREMIUM', 'platinum': '💎 PLATINUM', 'vip': '👑 VIP'}
TIER_LABELS_SITE = {'premium': '⭐ Premium', 'platinum': '💎 Platinum', 'vip': '👑 VIP-статус'}

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
            {'id': 'RUB', 'symbol': '🇷🇺', 'name': 'RUB'},
            {'id': 'USD', 'symbol': '🇺🇸', 'name': 'USD'},
            {'id': 'EUR', 'symbol': '🇪🇺', 'name': 'EUR'},
            {'id': 'TON', 'symbol': '💎', 'name': 'TON'},
            {'id': 'USDT', 'symbol': '💵', 'name': 'USDT'},
            {'id': 'STARS', 'symbol': '⭐', 'name': 'STARS'},
            {'id': 'BYN', 'symbol': '🇧🇾', 'name': 'BYN'},
            {'id': 'UAH', 'symbol': '🇺🇦', 'name': 'UAH'},
            {'id': 'KZT', 'symbol': '🇰🇿', 'name': 'KZT'},
            {'id': 'UZS', 'symbol': '🇺🇿', 'name': 'UZS'},
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

                conn = get_db()
                cur = conn.cursor()
                cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
                user = cur.fetchone()
                if not user:
                    conn.close()
                    return render(request, 'usersite/premium_wizard.html', {'step': 'error', 'error': 'Пользователь не найден'})

                # Проверяем баланс выбранной валюты
                bal_col = f"balance_{wizard_currency}"
                cur.execute(f"SELECT {bal_col} FROM users WHERE user_id=?", (user_id,))
                current_bal = (cur.fetchone() or [0])[0] or 0

                if current_bal >= price_in_currency:
                    cur.execute("BEGIN IMMEDIATE")
                    try:
                        cur.execute(f"UPDATE users SET {bal_col} = {bal_col} - ? WHERE user_id=?", (price_in_currency, user_id))
                        new_bal = current_bal - price_in_currency
                        if new_bal < 0:
                            conn.rollback()
                            conn.close()
                            return render(request, 'usersite/premium_wizard.html', {'step': 'error', 'error': 'Недостаточно средств'})
                        expires = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        cur.execute("""
                            UPDATE users SET
                                premium_tier = ?, is_premium = 1,
                                premium_until = ?, premium_granted_by = ?,
                                premium_granted_at = CURRENT_TIMESTAMP, premium_duration_days = ?
                            WHERE user_id = ?
                        """, (wizard_tier, expires, 0, wizard_days, user_id))
                        conn.commit()
                    except Exception as e:
                        conn.rollback()
                        conn.close()
                        return render(request, 'usersite/premium_wizard.html', {'step': 'error', 'error': f'Ошибка: {e}'})
                    conn.close()

                    # Очищаем сессию
                    for k in ['wizard_tier', 'wizard_currency', 'wizard_days']:
                        request.session.pop(k, None)
                    tier_badge = TIER_BADGES_MAP.get(wizard_tier, wizard_tier)
                    return render(request, 'usersite/premium_wizard.html', {'step': 'success', 'tier_badge': tier_badge, 'days': wizard_days, 'price': f"{fmt_price_site(price_in_currency)} {wizard_currency}"})

                # Cross-currency fallback
                total_user_rub = 0
                balances = {}
                for c in CURRENCIES:
                    b = float(user.get(f'balance_{c}', 0) or 0)
                    balances[c] = b
                    r = EXCHANGE_RATES.get(c, 1)
                    total_user_rub += b * r

                if total_user_rub < total_rub:
                    conn.close()
                    return render(request, 'usersite/premium_wizard.html', {'step': 'error', 'error': f'Недостаточно средств. Нужно ≈{total_rub:.0f} RUB, доступно ≈{total_user_rub:.0f} RUB'})

                remaining_rub = total_rub
                deduction_order = ["RUB", "USD", "EUR", "USDT", "TON", "STARS", "BYN", "UAH", "KZT", "UZS"]
                deduction_plan = {}
                cur.execute("BEGIN IMMEDIATE")
                try:
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

                    expires = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    cur.execute("""
                        UPDATE users SET
                            premium_tier = ?, is_premium = 1,
                            premium_until = ?, premium_granted_by = ?,
                            premium_granted_at = CURRENT_TIMESTAMP, premium_duration_days = ?
                        WHERE user_id = ?
                    """, (wizard_tier, expires, 0, wizard_days, user_id))
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    conn.close()
                    return render(request, 'usersite/premium_wizard.html', {'step': 'error', 'error': f'Ошибка: {e}'})
                conn.close()

                for k in ['wizard_tier', 'wizard_currency', 'wizard_days']:
                    request.session.pop(k, None)
                tier_badge = TIER_BADGES_MAP.get(wizard_tier, wizard_tier)
                plan_parts = [f"{fmt_price_site(a)} {c}" for c, a in deduction_plan.items()]
                return render(request, 'usersite/premium_wizard.html', {'step': 'success', 'tier_badge': tier_badge, 'days': wizard_days, 'cross_plan': ' + '.join(plan_parts), 'total_rub': f'{total_rub:.0f}'})

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

    # VIP routing
    cur.execute("SELECT premium_tier FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    tier = (row and row[0]) or 'free'
    is_vip = tier == 'vip'
    vip_tag = '[VIP] ' if is_vip else ''

    cur.execute(
        "INSERT INTO support_tickets (user_id, subject, category, user_type, order_number) VALUES (?,?,?,?,?)",
        (user_id, f"{vip_tag}{subject}", subject, user_type, order_number)
    )
    ticket_id = cur.lastrowid
    cur.execute(
        "INSERT INTO support_ticket_messages (ticket_id, sender_type, sender_name, message) VALUES (?,'user',?,?)",
        (ticket_id, user_login or f'User {user_id}', message)
    )

    if is_vip:
        # VIP-тикет автоматом назначается на первого свободного админа
        cur.execute("""
            UPDATE support_tickets SET assigned_to = (
                SELECT id FROM auth_user WHERE is_staff = 1 AND is_active = 1
                AND id NOT IN (SELECT assigned_to FROM support_tickets WHERE status = 'open')
                LIMIT 1
            ), priority = 'high' WHERE id = ?
        """, (ticket_id,))

    conn.commit()
    conn.close()
    return JsonResponse({'success': True, 'ticket_id': ticket_id, 'vip': is_vip})


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


@safe_db
def transactions_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')
    user_id = request.session.get('user_id')
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY created_at DESC LIMIT 50", (user_id,))
        rows = cur.fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()
    transactions = []
    for t in rows:
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
    return render(request, 'usersite/transactions.html', {
        'transactions': transactions,
    })


def withdraw_view(request):
    if not check_auth(request):
        return redirect('/usersite/login/')
    user_id = request.session.get('user_id')
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = cur.fetchone()
    balances = {}
    total_rub = 0
    for c in CURRENCIES:
        val = float(user.get(f'balance_{c}', 0) or 0) if user else 0
        if val > 0:
            rate = EXCHANGE_RATES.get(c, 1)
            rub_val = val * rate
            balances[c] = {'amount': val, 'symbol': CURRENCY_SYMBOLS.get(c, c), 'rub_value': rub_val}
            total_rub += rub_val
    tier = (user and (user['premium_tier'] or 'free')) or 'free' if user else 'free'
    commission_pct = TIER_COMMISSION.get(tier, 4)
    net_rub = round(total_rub * (1 - commission_pct / 100), 2)
    cur.execute("SELECT * FROM withdrawal_requests WHERE user_id=? ORDER BY created_at DESC LIMIT 20", (user_id,))
    requests = cur.fetchall()
    conn.close()
    return render(request, 'usersite/withdraw.html', {
        'requests': [dict(r) for r in requests],
        'balances': balances,
        'total_rub': round(total_rub, 2),
        'tier': tier,
        'tier_badge': TIER_BADGES.get(tier, '⬜ FREE'),
        'commission_pct': commission_pct,
        'net_rub': net_rub,
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

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = cur.fetchone()
    if not user:
        conn.close()
        return JsonResponse({'success': False, 'error': 'Пользователь не найден'}, status=404)

    total_rub = 0
    for c in CURRENCIES:
        val = float(user.get(f'balance_{c}', 0) or 0)
        total_rub += val * EXCHANGE_RATES.get(c, 1)

    tier = user.get('premium_tier', 'free') or 'free'
    commission_pct = TIER_COMMISSION.get(tier, 4)
    net_rub = total_rub * (1 - commission_pct / 100)

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
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT r.*, d.item AS deal_item, u.username AS reviewer_name FROM reviews r "
                "LEFT JOIN deals d ON r.deal_id = d.id "
                "LEFT JOIN users u ON r.reviewer_id = u.user_id "
                "WHERE r.reviewed_id = ? ORDER BY r.created_at DESC LIMIT 50", (user_id,))
    received = cur.fetchall()

    cur.execute("SELECT r.*, d.item AS deal_item, u.username AS reviewed_name FROM reviews r "
                "LEFT JOIN deals d ON r.deal_id = d.id "
                "LEFT JOIN users u ON r.reviewed_id = u.user_id "
                "WHERE r.reviewer_id = ? ORDER BY r.created_at DESC LIMIT 50", (user_id,))
    given = cur.fetchall()

    cur.execute(
        "SELECT AVG(rating) as avg_rating, COUNT(*) as total, "
        "SUM(CASE WHEN rating >= 4 THEN 1 ELSE 0 END) as positive "
        "FROM reviews WHERE reviewed_id = ?", (user_id,))
    stats_row = cur.fetchone()
    avg_rating = round(stats_row[0] or 0, 1) if stats_row else 0
    total = stats_row[1] or 0 if stats_row else 0
    positive = stats_row[2] or 0 if stats_row else 0
    positive_pct = round(positive / total * 100, 1) if total > 0 else 0

    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = cur.fetchone()
    conn.close()

    return render(request, 'usersite/reviews.html', {
        'user': dict(user) if user else {},
        'received': [dict(r) for r in received],
        'given': [dict(r) for r in given],
        'avg_rating': avg_rating,
        'total': total,
        'positive_pct': positive_pct,
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
