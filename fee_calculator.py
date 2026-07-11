import os
from decimal import Decimal
import logging
import sqlite3
from bot_config import VOLUME_FEE_TIERS
from currency_api import currency_api as _ca

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'novixgift.db')


def get_user_gmv_rub_30d(user_id: int) -> Decimal:
    """30-day completed deal volume in RUB equivalent (seller side)."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rates = _ca.get_stale_cache("RUB")
    cur.execute(
        "SELECT amount, currency FROM deals WHERE seller=? AND status='completed' AND completed >= datetime('now', '-30 days')",
        (user_id,)
    )
    total = Decimal('0')
    for row in cur.fetchall():
        amt = Decimal(str(row['amount'] or 0))
        cur_code = row['currency'] or 'RUB'
        if cur_code == 'RUB':
            total += amt
        else:
            rate = Decimal(str(rates.get(cur_code, 0)))
            if rate > 0:
                total += amt * rate
    conn.close()
    return total


def get_user_fee_rate(user_id: int) -> Decimal:
    """Effective deal commission rate based on 30-day seller GMV."""
    gmv = get_user_gmv_rub_30d(user_id)
    rate = Decimal(str(VOLUME_FEE_TIERS[0][1]))
    for threshold, r in VOLUME_FEE_TIERS:
        if gmv >= Decimal(str(threshold)):
            rate = Decimal(str(r))
    return rate


def get_user_volume_tier_info(user_id: int) -> dict:
    """Tier info: current rate, GMV, next threshold, progress."""
    gmv = get_user_gmv_rub_30d(user_id)
    current_rate = Decimal(str(VOLUME_FEE_TIERS[0][1]))
    current_idx = 0
    next_threshold = None
    next_rate = None
    for i, (threshold, r) in enumerate(VOLUME_FEE_TIERS):
        if gmv >= Decimal(str(threshold)):
            current_rate = Decimal(str(r))
            current_idx = i
        else:
            if next_threshold is None:
                next_threshold = threshold
                next_rate = r
            break
    else:
        if gmv >= Decimal(str(VOLUME_FEE_TIERS[-1][0])):
            current_rate = Decimal(str(VOLUME_FEE_TIERS[-1][1]))
            current_idx = len(VOLUME_FEE_TIERS) - 1

    prev_threshold = 0
    if next_threshold is not None:
        for threshold, _ in VOLUME_FEE_TIERS:
            if threshold < next_threshold:
                prev_threshold = threshold
    progress = 0
    if next_threshold is not None and next_threshold > prev_threshold:
        num_gmv = float(gmv)
        num_prev = float(prev_threshold)
        num_next = float(next_threshold)
        progress = (num_gmv - num_prev) / (num_next - num_prev) * 100
        progress = min(100, max(0, progress))

    return {
        'gmv_rub': round(float(gmv), 2),
        'current_rate': float(current_rate),
        'current_rate_pct': int(current_rate * 100),
        'tier_index': current_idx,
        'next_threshold': next_threshold,
        'next_rate': next_rate,
        'next_rate_pct': int(next_rate * 100) if next_rate else None,
        'progress_pct': round(progress, 1),
        'is_max_tier': next_threshold is None,
    }
