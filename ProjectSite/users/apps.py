import os, sqlite3
from django.apps import AppConfig

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, '..', 'novixgift.db')


def ensure_tables():
    conn = sqlite3.connect(DB_PATH, timeout=40)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            admin_id INTEGER,
            action TEXT,
            target_id INTEGER,
            amount REAL,
            ip_address TEXT DEFAULT '—'
        )
    """)
    try:
        cur.execute("ALTER TABLE admin_logs ADD COLUMN ip_address TEXT DEFAULT '—'")
    except Exception:
        pass
    conn.commit()
    conn.close()


class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'users'

    def ready(self):
        ensure_tables()
