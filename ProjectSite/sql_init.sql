CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    balance_RUB REAL DEFAULT 0,
    balance_USD REAL DEFAULT 0,
    balance_EUR REAL DEFAULT 0,
    balance_BYN REAL DEFAULT 0,
    balance_UAH REAL DEFAULT 0,
    balance_KZT REAL DEFAULT 0,
    balance_UZS REAL DEFAULT 0,
    balance_TON REAL DEFAULT 0,
    balance_USDT REAL DEFAULT 0,
    balance_STARS REAL DEFAULT 0,
    is_premium INTEGER DEFAULT 0,
    premium_tier TEXT DEFAULT 'free',
    premium_until TIMESTAMP,
    premium_duration_days INTEGER DEFAULT 0,
    premium_granted_by INTEGER DEFAULT 0,
    premium_granted_at TIMESTAMP,
    rating REAL DEFAULT 0,
    reviews_count INTEGER DEFAULT 0,
    referral_code TEXT,
    referred_by INTEGER,
    referral_earnings REAL DEFAULT 0,
    notifications_enabled INTEGER DEFAULT 1,
    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_admin INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS promocodes (
    code TEXT PRIMARY KEY,
    amount REAL,
    max_uses INTEGER DEFAULT 1,
    used_count INTEGER DEFAULT 0,
    expires_at TEXT,
    active INTEGER DEFAULT 1,
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_by INTEGER,
    deleted_at TIMESTAMP,
    delete_reason TEXT
);

CREATE TABLE IF NOT EXISTS deals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller INTEGER,
    buyer INTEGER,
    item TEXT,
    amount REAL,
    commission REAL,
    currency TEXT DEFAULT 'RUB',
    status TEXT DEFAULT 'awaiting',
    created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed TIMESTAMP,
    payment_method TEXT DEFAULT 'internal',
    payment_comment TEXT,
    payment_address TEXT,
    payment_amount REAL,
    paid_tx_hash TEXT,
    paid_at TIMESTAMP,
    disputed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS admin_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER,
    action TEXT,
    target_id INTEGER,
    amount REAL,
    ip_address TEXT DEFAULT '—',
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id INTEGER,
    reviewer_id INTEGER,
    reviewed_id INTEGER,
    rating INTEGER,
    comment TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_moderated INTEGER DEFAULT 0,
    moderated_by INTEGER,
    moderated_at TIMESTAMP,
    reported INTEGER DEFAULT 0,
    report_reason TEXT
);
