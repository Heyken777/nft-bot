"""
risk_engine.py — общий модуль антифрода: скоринг сделок,
связка аккаунтов (fingerprint/IP) и cooldown вывода.

Импортируется из bot.py (корень) и Django (ProjectSite) — ничего
не зависит от фреймворка, только от sqlite3-connection и cursor.
Все функции принимают (cur, ...) — транзакционность на вызывающей стороне.

Важно: скоринг НИКОГДА не блокирует сделки автоматически.
Только ставит flagged=1 для ручной проверки админом.
"""
import ipaddress
import re
from datetime import datetime, timedelta

try:
    from bot_config import (
        NEW_ACCOUNT_MIN_AGE_DAYS,
        NEW_ACCOUNT_WITHDRAW_LIMIT_RUB,
        RISK_FLAG_THRESHOLD,
        RISK_FP_LINK_WINDOW_DAYS,
    )
except ImportError:  # fallback для окружений без env
    NEW_ACCOUNT_MIN_AGE_DAYS = 7
    NEW_ACCOUNT_WITHDRAW_LIMIT_RUB = 5000.0
    RISK_FLAG_THRESHOLD = 50
    RISK_FP_LINK_WINDOW_DAYS = 30

FP_HASH_RE = re.compile(r'^[a-f0-9]{64}$')
OWNER_TELEGRAM_ID = 1803437347  # CEO обходит cooldown

_UBG_RATES = {'RUB': 1.0, 'USD': 73.0, 'EUR': 83.0, 'BYN': 26.0, 'UAH': 1.6,
              'KZT': 0.15, 'UZS': 0.0061, 'TON': 120.0, 'USDT': 73.0, 'STARS': 2.0}


def valid_fingerprint_hash(value) -> bool:
    """Не доверяем клиенту: только 64 hex-символа (SHA-256)."""
    return isinstance(value, str) and bool(FP_HASH_RE.match(value))


def _account_age_days(cur, user_id):
    cur.execute("SELECT created_at FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row or not row[0]:
        return None
    created = str(row[0])
    try:
        dt = datetime.strptime(created[:19], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        try:
            dt = datetime.fromisoformat(created[:19].replace('T', ' '))
        except ValueError:
            return None
    return (datetime.utcnow() - dt).days


def get_user_fingerprints(cur, user_id):
    """Все fingerprint_hash данного пользователя (последняя активная сессия)."""
    cur.execute(
        "SELECT DISTINCT fingerprint_hash FROM known_devices "
        "WHERE user_id=? AND fingerprint_hash IS NOT NULL AND fingerprint_hash != ''",
        (user_id,)
    )
    return [r[0] for r in cur.fetchall()]


def get_linked_accounts(cur, user_id, ip_window_days=None):
    """
    Аккаунты, связанные с user_id:
      - общий fingerprint_hash (другой user_id);
      - пересечение по IP за последние ip_window_days дней.
    Возвращает dict: {user_id: {reason, related_fp}}.
    Переиспользуется задачей антиреферальной защиты.
    """
    if ip_window_days is None:
        ip_window_days = RISK_FP_LINK_WINDOW_DAYS
    linked = {}

    fps = get_user_fingerprints(cur, user_id)
    for fp in fps:
        cur.execute(
            "SELECT user_id FROM known_devices "
            "WHERE fingerprint_hash=? AND user_id!=? GROUP BY user_id",
            (fp, user_id)
        )
        for r in cur.fetchall():
            linked.setdefault(r[0], {}).update({'reason': 'fingerprint', 'related_fp': fp})

    cur.execute(
        "SELECT DISTINCT ip_address FROM known_devices WHERE user_id=? "
        "AND ip_address IS NOT NULL AND ip_address != '' AND ip_address != '0.0.0.0'",
        (user_id,)
    )
    ips = [r[0] for r in cur.fetchall()]
    if ips:
        window_ts = (datetime.utcnow() - timedelta(days=ip_window_days)).strftime('%Y-%m-%d %H:%M:%S')
        qmarks = ','.join('?' * len(ips))
        cur.execute(
            f"SELECT user_id FROM known_devices "
            f"WHERE ip_address IN ({qmarks}) AND user_id!=? AND last_seen>=? GROUP BY user_id",
            ips + [user_id, window_ts]
        )
        for r in cur.fetchall():
            existing = linked.get(r[0])
            if existing:
                existing['reason'] = 'fp+ip' if existing.get('reason') == 'fingerprint' else 'ip'
            else:
                linked[r[0]] = {'reason': 'ip', 'related_fp': None}

    return linked


def has_same_fingerprint(cur, user_a, user_b):
    """Совпадает ли хоть один fingerprint_hash между двумя пользователями."""
    fps_a = get_user_fingerprints(cur, user_a)
    if not fps_a:
        return False
    qmarks = ','.join('?' * len(fps_a))
    cur.execute(
        f"SELECT 1 FROM known_devices WHERE user_id=? AND fingerprint_hash IN ({qmarks}) LIMIT 1",
        (user_b,) + tuple(fps_a)
    )
    return cur.fetchone() is not None


def has_ip_overlap(cur, user_a, user_b, ip_window_days=None):
    if ip_window_days is None:
        ip_window_days = RISK_FP_LINK_WINDOW_DAYS
    cur.execute(
        "SELECT DISTINCT ip_address FROM known_devices WHERE user_id=? "
        "AND ip_address IS NOT NULL AND ip_address != '' AND ip_address != '0.0.0.0'",
        (user_a,)
    )
    ips = [r[0] for r in cur.fetchall()]
    if not ips:
        return False
    qmarks = ','.join('?' * len(ips))
    window_ts = (datetime.utcnow() - timedelta(days=ip_window_days)).strftime('%Y-%m-%d %H:%M:%S')
    cur.execute(
        f"SELECT 1 FROM known_devices WHERE user_id=? AND ip_address IN ({qmarks}) AND last_seen>=? LIMIT 1",
        (user_b,) + tuple(ips) + (window_ts,)
    )
    return cur.fetchone() is not None


def _avg_deal_amount(cur, user_id):
    cur.execute(
        "SELECT AVG(amount) FROM deals WHERE seller=? AND amount>0",
        (user_id,)
    )
    row = cur.fetchone()
    return float(row[0]) if row and row[0] else 0.0


def compute_deal_risk(cur, seller_id, buyer_id=None, amount=0.0, currency='RUB',
                      rates=None):
    """
    Скоринг риска сделки. Возвращает (score:int, reasons:list[str]).
    Поднимает очки, НЕ блокирует. Вызывается при создании сделки и при отклике.
    """
    score = 0
    reasons = []
    if rates is None:
        rates = _UBG_RATES
    amount_rub = float(amount) * float(rates.get(currency, 1))

    # 1. Возраст аккаунтов
    for uid, label in ((seller_id, 'Продавец'), (buyer_id, 'Покупатель')):
        if not uid:
            continue
        age = _account_age_days(cur, uid)
        if age is None:
            continue
        if age < 7:
            score += 20
            reasons.append(f'{label}: аккаунт младше 7 дней')
        elif age < 30:
            score += 10
            reasons.append(f'{label}: аккаунт младше 30 дней')

    # 2. Первая сделка с контрагентом
    if buyer_id:
        cur.execute(
            "SELECT COUNT(*) FROM deals WHERE seller=? AND buyer=?",
            (seller_id, buyer_id)
        )
        prior = (cur.fetchone() or [0])[0] or 0
        if prior == 0:
            score += 15
            reasons.append('Первая сделка между контрагентами')

    # 3. Сумма значительно выше среднего по аккаунту
    avg = _avg_deal_amount(cur, seller_id)
    if avg > 0 and amount_rub > avg * 5:
        score += 15
        reasons.append(f'Сумма > 5× средней ({avg:.0f} RUB)')

    # 4. Крупная сумма
    if amount_rub > 100_000:
        score += 10
        reasons.append('Сумма > 100 000 RUB')

    # 5. Самосделка: общий fingerprint
    if buyer_id and has_same_fingerprint(cur, seller_id, buyer_id):
        score += 50
        reasons.append('Fingerprint совпадает с контрагентом')

    # 6. Пересечение IP с контрагентом
    if buyer_id and has_ip_overlap(cur, seller_id, buyer_id):
        score += 25
        reasons.append('Совпадение IP с контрагентом (30 дней)')

    # 7. Есть связанные аккаунты
    linked = get_linked_accounts(cur, seller_id)
    if linked:
        score += 40
        reasons.append(f'Связанные аккаунты: {", ".join(map(str, sorted(linked)))}')

    return score, reasons


def withdrawal_cooldown_status(cur, user_id, rates=None):
    """
    Статус cooldown вывода для нового аккаунта.
    Возвращает dict:
      {in_cooldown: bool, min_age_days, limit_rub, used_today_rub, remaining_rub, account_age_days}
    """
    if rates is None:
        rates = _UBG_RATES
    age = _account_age_days(cur, user_id)
    in_cooldown = age is not None and age < NEW_ACCOUNT_MIN_AGE_DAYS

    used_today = 0.0
    if in_cooldown:
        today = datetime.utcnow().strftime('%Y-%m-%d')
        cur.execute(
            "SELECT COALESCE(SUM(amount),0) FROM withdrawal_requests "
            "WHERE user_id=? AND status IN ('approved','pending') AND created_at>=?",
            (user_id, today)
        )
        used_today = float(cur.fetchone()[0] or 0)

    limit = NEW_ACCOUNT_WITHDRAW_LIMIT_RUB
    return {
        'in_cooldown': in_cooldown,
        'min_age_days': NEW_ACCOUNT_MIN_AGE_DAYS,
        'limit_rub': limit,
        'used_today_rub': round(used_today, 2),
        'remaining_rub': round(max(0.0, limit - used_today), 2),
        'account_age_days': age,
    }