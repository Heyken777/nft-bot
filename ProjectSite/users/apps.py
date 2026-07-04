import os, sqlite3
from django.apps import AppConfig

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, '..', 'novixgift.db')


def ensure_tables():
    conn = sqlite3.connect(DB_PATH, timeout=40)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            user_id BIGINT,
            username TEXT DEFAULT '',
            action_type TEXT,
            description TEXT,
            ip_address TEXT DEFAULT '—'
        )
    """)
    conn.commit()
    conn.close()


OWNER_TELEGRAM_ID = int(os.getenv("OWNER_TELEGRAM_ID", "1803437347"))
CEO_USERNAME = os.getenv("CEO_USERNAME", "Arkadiex")


def seed_ceo_profile():
    conn = sqlite3.connect(DB_PATH, timeout=40)
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM users WHERE user_id=777 OR username='heyken'")
        cur.execute("SELECT user_id FROM users WHERE user_id=?", (OWNER_TELEGRAM_ID,))
        owner = cur.fetchone()
        if not owner:
            cur.execute("""
                INSERT INTO users (user_id, username, admin_role, premium_tier, premium_until)
                VALUES (?, ?, 'CEO', 'vip', '2036-01-01 00:00:00')
            """, (OWNER_TELEGRAM_ID, CEO_USERNAME))
            print(f"CEO profile @{CEO_USERNAME} created in DB")
        else:
            cur.execute("UPDATE users SET username=?, admin_role='CEO', premium_tier='vip', premium_until='2036-01-01 00:00:00' WHERE user_id=?",
                        (CEO_USERNAME, OWNER_TELEGRAM_ID))
            print(f"CEO profile @{CEO_USERNAME} synced")
        conn.commit()
    except Exception as e:
        print(f"CEO seed warning: {e}")
    finally:
        conn.close()


class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'users'

    def ready(self):
        ensure_tables()
        seed_ceo_profile()
