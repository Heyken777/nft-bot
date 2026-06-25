#!/usr/bin/env python3
"""
Скрипт миграции базы данных NovixGift Bot.
Безопасно добавляет новые колонки и таблицы, НЕ удаляя существующие данные.
Запуск: python migrate_db.py
"""

import sqlite3
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DB_PATH = "novixgift.db"


def column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(col[1] == column for col in cursor.fetchall())


def add_column(cursor, conn, table: str, column: str, col_type: str):
    if not column_exists(cursor, table, column):
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        logger.info(f"  + Добавлена колонка {table}.{column} ({col_type})")
        return True
    else:
        logger.info(f"  = Колонка {table}.{column} уже существует")
        return False


def table_exists(cursor, table: str) -> bool:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None


def migrate():
    logger.info("=" * 50)
    logger.info("Запуск миграции БД NovixGift Bot...")
    logger.info("=" * 50)

    if not os.path.exists(DB_PATH):
        logger.error(f"Файл БД {DB_PATH} не найден! Создайте его, запустив бота хотя бы раз.")
        return False

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    changes = False

    # ===== 1. Таблица users =====
    logger.info("\n[1] Таблица users")
    user_columns = {
        "card_currency": "TEXT DEFAULT 'RUB'",
        "is_premium": "INTEGER DEFAULT 0",
        "premium_until": "TIMESTAMP",
        "rating": "REAL DEFAULT 0",
        "reviews_count": "INTEGER DEFAULT 0",
        "referral_code": "TEXT",
        "referred_by": "INTEGER",
        "referral_earnings": "REAL DEFAULT 0",
        "notifications_enabled": "INTEGER DEFAULT 1",
        "premium_granted_by": "INTEGER DEFAULT 0",
        "premium_granted_at": "TIMESTAMP",
        "premium_duration_days": "INTEGER DEFAULT 0",
        "last_activity": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ton": "TEXT",
    }
    for col, col_type in user_columns.items():
        if add_column(cursor, conn, "users", col, col_type):
            changes = True

    # Балансы для всех валют
    for curr in ["RUB", "BYN", "UAH", "KZT", "UZS", "EUR", "USD", "TON", "USDT", "STARS"]:
        if add_column(cursor, conn, "users", f"balance_{curr}", "REAL DEFAULT 0"):
            changes = True

    # ===== 2. Таблица deals =====
    logger.info("\n[2] Таблица deals")
    deal_columns = {
        "payment_method": "TEXT DEFAULT 'internal'",
        "payment_comment": "TEXT",
        "payment_address": "TEXT",
        "payment_amount": "REAL",
        "paid_tx_hash": "TEXT",
        "paid_at": "TIMESTAMP",
        "disputed_at": "TIMESTAMP",
    }
    for col, col_type in deal_columns.items():
        if add_column(cursor, conn, "deals", col, col_type):
            changes = True

    # ===== 3. Таблица promocodes =====
    logger.info("\n[3] Таблица promocodes")
    promo_columns = {
        "created_by": "INTEGER",
        "created_at": "TIMESTAMP",
        "deleted_by": "INTEGER",
        "deleted_at": "TIMESTAMP",
        "delete_reason": "TEXT",
    }
    for col, col_type in promo_columns.items():
        if add_column(cursor, conn, "promocodes", col, col_type):
            changes = True

    # ===== 4. Новые таблицы =====
    logger.info("\n[4] Новые таблицы")

    new_tables = {
        "friend_promo_activations": """
            CREATE TABLE IF NOT EXISTS friend_promo_activations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                user_id INTEGER,
                activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id)
            )
        """,
        "referral_deposit_log": """
            CREATE TABLE IF NOT EXISTS referral_deposit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                user_id INTEGER,
                currency TEXT,
                deposit_amount REAL,
                reward_amount REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """,
    }

    for table_name, create_sql in new_tables.items():
        if not table_exists(cursor, table_name):
            cursor.execute(create_sql)
            logger.info(f"  + Создана таблица {table_name}")
            changes = True
        else:
            logger.info(f"  = Таблица {table_name} уже существует")

    # Фиксация изменений
    if changes:
        conn.commit()
        logger.info("\n" + "=" * 50)
        logger.info("Миграция завершена! Все изменения сохранены.")
        logger.info("=" * 50)
    else:
        logger.info("\n" + "=" * 50)
        logger.info("БД актуальна. Изменений не требуется.")
        logger.info("=" * 50)

    conn.close()
    return True


if __name__ == "__main__":
    migrate()
