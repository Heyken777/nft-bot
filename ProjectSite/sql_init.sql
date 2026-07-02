CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    balance REAL DEFAULT 0,
    card_details TEXT,
    ton_wallet TEXT,
    is_admin INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    card_currency TEXT DEFAULT 'RUB',
    is_premium INTEGER DEFAULT 0,
    premium_until TIMESTAMP,
    rating REAL DEFAULT 0,
    reviews_count INTEGER DEFAULT 0,
    referral_code TEXT,
    referred_by INTEGER,
    referral_earnings REAL DEFAULT 0,
    notifications_enabled INTEGER DEFAULT 1,
    premium_granted_by INTEGER DEFAULT 0,
    premium_granted_at TIMESTAMP,
    premium_duration_days INTEGER DEFAULT 0,
    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ton TEXT,
    balance_RUB REAL DEFAULT 0,
    balance_BYN REAL DEFAULT 0,
    balance_UAH REAL DEFAULT 0,
    balance_KZT REAL DEFAULT 0,
    balance_UZS REAL DEFAULT 0,
    balance_EUR REAL DEFAULT 0,
    balance_USD REAL DEFAULT 0,
    balance_TON REAL DEFAULT 0,
    balance_USDT REAL DEFAULT 0,
    balance_STARS REAL DEFAULT 0,
    premium_tier TEXT DEFAULT 'free'
);

CREATE TABLE IF NOT EXISTS promocodes (
    code TEXT PRIMARY KEY,
    bonus_amount REAL,
    currency TEXT DEFAULT 'RUB',
    max_activations INTEGER DEFAULT 1,
    current_activations INTEGER DEFAULT 0,
    created_by TEXT,
    deleted_by TEXT,
    date_expired TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS deals (
    deal_id INTEGER PRIMARY KEY,
    buyer_id BIGINT,
    seller_id BIGINT,
    guarantor_id BIGINT,
    amount REAL,
    currency TEXT DEFAULT 'RUB',
    asset_name TEXT,
    status TEXT DEFAULT 'awaiting',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT DEFAULT (datetime('now')),
    user_id BIGINT,
    username TEXT,
    action_type TEXT,
    description TEXT,
    ip_address TEXT DEFAULT '—'
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id INTEGER,
    from_user_id BIGINT,
    to_user_id BIGINT,
    rating INTEGER,
    comment TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_moderated INTEGER DEFAULT 0,
    UNIQUE(deal_id, from_user_id)
);
