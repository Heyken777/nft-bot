import asyncio
import sqlite3
import logging
import random
import secrets
import hashlib
import hmac
import io
from decimal import Decimal, ROUND_DOWN
from urllib.parse import parse_qsl
import aiohttp
import json
import re
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, List, Dict
from bot_config import *
from currency_api import currency_api
from aiohttp import ClientTimeout
import qrcode

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, FSInputFile, InputMediaPhoto
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

WEBAPP_URL = "http://93.115.101.179:9207"

# ========== КОНФИГУРАЦИЯ PREMIUM ==========
PREMIUM_RATES = {
    "RUB": 1,
    "USD": 73,
    "EUR": 83,
    "TON": 120,
    "USDT": 73,
    "STARS": 2,
    "UAH": 1.6,
    "KZT": 0.15,
    "UZS": 0.0061,
    "BYN": 26,
}

PREMIUM_PRICES_RUB = {
    30: 299,
    45: 419,
    60: 559,
    90: 799,
    365: 2999
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

deal_lock = asyncio.Lock()  # CHANGED: глобальная блокировка для атомарных операций по сделкам/балансам в SQLite
ton_monitor_task = None  # CHANGED: фоновая задача мониторинга входящих TON/USDT платежей
db_batch_lock = asyncio.Lock()  # CHANGED: блокировка для массовых атомарных операций с БД


def quantize_amount(value, places: str = "0.000001") -> float:
    return float(Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_DOWN))


def generate_payment_comment(deal_id: int, buyer_id: int) -> str:
    return f"NOVIX-{deal_id}-{buyer_id}"


def verify_telegram_webapp_init_data(init_data: str) -> Optional[Dict]:
    """CHANGED: backend-проверка подписи initData для Telegram Mini App."""
    if not init_data or not BOT_TOKEN:
        return None

    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None

        data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        auth_date = int(parsed.get("auth_date", "0"))
        if not auth_date:
            return None
        auth_dt = datetime.fromtimestamp(auth_date, tz=timezone.utc)
        if datetime.now(timezone.utc) - auth_dt > timedelta(seconds=MINI_APP_AUTH_MAX_AGE):
            return None

        user_raw = parsed.get("user")
        if not user_raw:
            return None

        return json.loads(user_raw)
    except Exception as e:
        logger.warning(f"Mini App auth validation failed: {e}")
        return None


async def get_authenticated_webapp_user(request) -> Optional[Dict]:
    """CHANGED: извлекает и проверяет initData из Authorization или X-Telegram-Init-Data."""
    auth_header = request.headers.get("Authorization", "")
    init_data = ""
    if auth_header.startswith("tma "):
        init_data = auth_header[4:].strip()
    elif auth_header.startswith("Bearer "):
        init_data = auth_header[7:].strip()
    else:
        init_data = request.headers.get("X-Telegram-Init-Data", "").strip()

    return verify_telegram_webapp_init_data(init_data)


async def notify_user(user_id: int, text: str, parse_mode: str = "Markdown", reply_markup=None):
    """CHANGED: единая функция push-уведомлений в Telegram + запись в локальные notifications."""
    db.add_notification(user_id, text)
    try:
        await bot.send_message(user_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception as e:
        logger.warning(f"Не удалось отправить push-уведомление пользователю {user_id}: {e}")


def escape_md(text: str) -> str:
    chars = r'[_*[\]()~`>#+\-=|{}.!]'
    return re.sub(f'([{re.escape(chars)}])', r'\\\1', str(text))

def fmt_num(num: float) -> str:
    return f"{num:,.0f}".replace(",", " ")

def img_path(name: str) -> str:
    return os.path.join(IMAGES_PATH, name)

def img_exists(name: str) -> bool:
    return os.path.exists(img_path(name))

async def edit_or_new(call, text: str, reply_markup, photo_name: str = None):
    try:
        if photo_name and img_exists(photo_name):
            photo = FSInputFile(img_path(photo_name))
            await call.message.edit_media(
                InputMediaPhoto(media=photo, caption=text, parse_mode="Markdown"),
                reply_markup=reply_markup
            )
        else:
            await call.message.edit_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    except:
        await call.message.delete()
        if photo_name and img_exists(photo_name):
            photo = FSInputFile(img_path(photo_name))
            await call.message.answer_photo(photo=photo, caption=text, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            await call.message.answer(text, parse_mode="Markdown", reply_markup=reply_markup)

async def answer_or_edit(msg, text: str, reply_markup, photo_name: str = None):
    if photo_name and img_exists(photo_name):
        photo = FSInputFile(img_path(photo_name))
        await msg.answer_photo(photo=photo, caption=text, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await msg.answer(text, parse_mode="Markdown", reply_markup=reply_markup)

def main_kb(is_admin: bool = False):
    kb = [
        [InlineKeyboardButton(text="📱 Открыть приложение", url=f"{WEBAPP_URL}/usersite/")],
        [InlineKeyboardButton(text="💰 Пополнить", callback_data="deposit"),
        InlineKeyboardButton(text="💸 Вывести", callback_data="withdraw")],
        [InlineKeyboardButton(text="💳 Карта", callback_data="set_card"),
        InlineKeyboardButton(text="📱 TON", callback_data="set_ton")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
        InlineKeyboardButton(text="⭐ Premium", callback_data="buy_premium")],
        [InlineKeyboardButton(text="➕ Создать сделку", callback_data="create_deal"),
        InlineKeyboardButton(text="📋 Мои сделки", callback_data="my_deals")],
        [InlineKeyboardButton(text="👥 Рефералы", callback_data="referral")]
    ]
    if is_admin:
        kb.append([InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="menu")]])

def cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]])

def admin_cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")]])

def deal_kb(deal_id: int, role: str, status: str):
    kb = []
    if role == "buyer" and status == "awaiting":
        kb.append([InlineKeyboardButton(text="✅ Оплатить", callback_data=f"pay_{deal_id}")])
        kb.append([InlineKeyboardButton(text="⚠️ Открыть спор", callback_data=f"dispute_{deal_id}")])
    elif role == "buyer" and status == "payment_pending":
        kb.append([InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"pay_{deal_id}")])
        kb.append([InlineKeyboardButton(text="⚠️ Открыть спор", callback_data=f"dispute_{deal_id}")])
    elif role == "seller" and status == "paid":
        kb.append([InlineKeyboardButton(text="📦 Товар передан", callback_data=f"sent_{deal_id}")])
        kb.append([InlineKeyboardButton(text="⚠️ Открыть спор", callback_data=f"dispute_{deal_id}")])
    elif role == "buyer" and status == "item_sent":
        kb.append([InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"recv_{deal_id}")])
        kb.append([InlineKeyboardButton(text="⚠️ Открыть спор", callback_data=f"dispute_{deal_id}")])
    elif status == "disputed":
        kb.append([InlineKeyboardButton(text="⚖️ Спор открыт", callback_data=f"dispute_{deal_id}")])
    kb.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def share_kb(deal_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Поделиться", switch_inline_query=f"Сделка #{deal_id} - https://t.me/{BOT_USERNAME}?start=deal_{deal_id}")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
    ])

# ========== КОНФИГУРАЦИЯ ВАЛЮТ ==========
CURRENCIES = {
    "RUB": {"symbol": "🇷🇺", "name": "RUB"},
    "BYN": {"symbol": "🇧🇾", "name": "BYN"},
    "UAH": {"symbol": "🇺🇦", "name": "UAH"},
    "KZT": {"symbol": "🇰🇿", "name": "KZT"},
    "UZS": {"symbol": "🇺🇿", "name": "UZS"},
    "EUR": {"symbol": "🇪🇺", "name": "EUR"},
    "USD": {"symbol": "🇺🇸", "name": "USD"},
    "TON": {"symbol": "💎", "name": "TON"},
    "USDT": {"symbol": "💵", "name": "USDT"},
    "STARS": {"symbol": "⭐️", "name": "STARS"}
}

def card_currency_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 RUB", callback_data="card_cur_RUB"),
        InlineKeyboardButton(text="🇧🇾 BYN", callback_data="card_cur_BYN"),
        InlineKeyboardButton(text="🇺🇦 UAH", callback_data="card_cur_UAH")],
        [InlineKeyboardButton(text="🇰🇿 KZT", callback_data="card_cur_KZT"),
        InlineKeyboardButton(text="🇺🇿 UZS", callback_data="card_cur_UZS"),
        InlineKeyboardButton(text="🇪🇺 EUR", callback_data="card_cur_EUR")],
        [InlineKeyboardButton(text="🇺🇸 USD", callback_data="card_cur_USD")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu")]   
    ])

def premium_currency_kb():
    currencies = ["RUB", "USD", "EUR", "TON", "USDT", "STARS", "BYN", "UAH", "KZT", "UZS"]
    kb = []
    row = []
    for curr in currencies:
        info = CURRENCIES[curr]
        label = f"{info['symbol']} {info['name']}"
        row.append(InlineKeyboardButton(text=label, callback_data=f"premium_cur_{curr}"))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def currency_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 RUB", callback_data="cur_RUB"),
        InlineKeyboardButton(text="🇺🇸 USD", callback_data="cur_USD"),
        InlineKeyboardButton(text="🇪🇺 EUR", callback_data="cur_EUR")],
        [InlineKeyboardButton(text="🇺🇦 UAH", callback_data="cur_UAH"),
        InlineKeyboardButton(text="🇰🇿 KZT", callback_data="cur_KZT"),
        InlineKeyboardButton(text="🇺🇿 UZS", callback_data="cur_UZS")],
        [InlineKeyboardButton(text="🇧🇾 BYN", callback_data="cur_BYN"),
        InlineKeyboardButton(text="💎 TON", callback_data="cur_TON"),
        InlineKeyboardButton(text="💵 USDT", callback_data="cur_USDT")],
        [InlineKeyboardButton(text="⭐ Stars", callback_data="cur_STARS")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu")]
    ])

def admin_currency_kb(action: str, user_id: int, back_to_user: bool = False):
    currencies = ["RUB", "BYN", "UAH", "KZT", "UZS", "EUR", "USD", "TON", "USDT", "STARS"]
    kb = []
    row = []
    for i, curr in enumerate(currencies):
        row.append(InlineKeyboardButton(text=CURRENCIES[curr]["symbol"], callback_data=f"{action}_{curr}_{user_id}"))
        if len(row) == 3:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    back_callback = f"admin_back_user_{user_id}" if back_to_user else "admin_panel"
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def admin_back_kb(uid: int = None):
    """Клавиатура Отмены: если uid передан — возвращает в карточку пользователя, иначе в админ-панель"""
    if uid:
        back_data = f"admin_back_user_{uid}"
    else:
        back_data = "admin_panel"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=back_data)]
    ])

def premium_days_kb(user_id: int, back_to_user: bool = False):
    back_callback = "premium_back_user" if back_to_user else "admin_panel"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 30 дней (299 RUB)", callback_data=f"premium_days_30_{user_id}"),
        InlineKeyboardButton(text="📅 45 дней (419 RUB)", callback_data=f"premium_days_45_{user_id}")],
        [InlineKeyboardButton(text="📅 60 дней (559 RUB)", callback_data=f"premium_days_60_{user_id}"),
        InlineKeyboardButton(text="📅 90 дней (799 RUB)", callback_data=f"premium_days_90_{user_id}")],
        [InlineKeyboardButton(text="👑 FOREVER (1999 RUB)", callback_data=f"premium_days_forever_{user_id}")],
        [InlineKeyboardButton(text="❌ Забрать Premium", callback_data=f"premium_remove_{user_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=back_callback)]
    ])

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Зачислить", callback_data="admin_credit"),
        InlineKeyboardButton(text="💸 Списать", callback_data="admin_debit")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users"),
        InlineKeyboardButton(text="📦 Активные сделки", callback_data="admin_deals")],
        [InlineKeyboardButton(text="⭐ Выдать Premium", callback_data="admin_premium"),
        InlineKeyboardButton(text="👑 Premium пользователи", callback_data="admin_premium_users")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_mailing"),
        InlineKeyboardButton(text="⚠️ Споры", callback_data="admin_disputes")],
        [InlineKeyboardButton(text="🎫 Промокоды", callback_data="admin_promocodes"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="menu")]
    ])

def get_user_role(deals_count: int) -> str:
    if deals_count >= 100:
        return "👑 Бог сделок"
    elif deals_count >= 50:
        return "💰 Скупщик"
    elif deals_count >= 25:
        return "⚡ Опытный трейдер"
    elif deals_count >= 10:
        return "📈 Активный пользователь"
    elif deals_count >= 5:
        return "🟢 Начинающий трейдер"
    else:
        return "🆕 Новичок"

# ========== БАЗА ДАННЫХ ==========
class Database:
    def __init__(self):
        self.conn = sqlite3.connect("novixgift.db", check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.init()

    def add_column_if_not_exists(self, table_name: str, column_name: str, column_type: str):
        try:
            self.cursor.execute(f"PRAGMA table_info({table_name})")
            existing_columns = [col[1] for col in self.cursor.fetchall()]
            if column_name not in existing_columns:
                self.cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                logger.info(f"Добавлена колонка {column_name} в таблицу {table_name}")
                self.conn.commit()
                return True
        except Exception as e:
            logger.warning(f"Не удалось добавить {column_name}: {e}")
        return False

    def init(self):
        # Создаём таблицу users
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance REAL DEFAULT 0,
                card_details TEXT,
                ton_wallet TEXT,
                is_admin INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Добавляем колонки с проверкой на существование
        columns_to_add = {
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
            "ton": "TEXT"
        }
        
        for col_name, col_type in columns_to_add.items():
            self.add_column_if_not_exists("users", col_name, col_type)
        
        # Добавляем балансы для всех валют
        for curr in ["RUB", "BYN", "UAH", "KZT", "UZS", "EUR", "USD", "TON", "USDT", "STARS"]:
            self.add_column_if_not_exists("users", f"balance_{curr}", "REAL DEFAULT 0")
        
        # Таблица сделок
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS deals (
                id INTEGER PRIMARY KEY,
                seller INTEGER,
                buyer INTEGER,
                item TEXT,
                amount REAL,
                commission REAL,
                currency TEXT DEFAULT 'RUB',
                status TEXT DEFAULT 'awaiting',
                created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed TIMESTAMP
            )
        """)
        self.add_column_if_not_exists("deals", "payment_method", "TEXT DEFAULT 'internal'")  # CHANGED: способ оплаты сделки
        self.add_column_if_not_exists("deals", "payment_comment", "TEXT")  # CHANGED: уникальный payload/comment для TON/USDT
        self.add_column_if_not_exists("deals", "payment_address", "TEXT")  # CHANGED: escrow-адрес для on-chain оплаты
        self.add_column_if_not_exists("deals", "payment_amount", "REAL")  # CHANGED: сумма on-chain оплаты
        self.add_column_if_not_exists("deals", "paid_tx_hash", "TEXT")  # CHANGED: tx hash подтвержденного платежа
        self.add_column_if_not_exists("deals", "paid_at", "TIMESTAMP")  # CHANGED: время подтверждения оплаты
        self.add_column_if_not_exists("deals", "disputed_at", "TIMESTAMP")  # CHANGED: время перевода в спор
        
        # Таблица заявок на вывод
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS withdrawal_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                wallet_type TEXT,
                wallet_address TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица отзывов
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id INTEGER,
                reviewer_id INTEGER,
                reviewed_id INTEGER,
                rating INTEGER,
                comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица споров
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS disputes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id INTEGER,
                opened_by INTEGER,
                reason TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица уведомлений
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                title TEXT,
                message TEXT,
                is_read INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица для рассылок
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS newsletters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                message TEXT,
                sent_count INTEGER DEFAULT 0,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица для защиты от флуда
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                user_id INTEGER PRIMARY KEY,
                session_token TEXT,
                expires_at TIMESTAMP
            )
        """)
        
        # Таблица логов админов
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                action TEXT,
                target_id INTEGER,
                amount REAL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица достижений пользователей
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_achievements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                achievement_id TEXT,
                earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                claimed INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        
        # Таблица для хранения информации о достижениях
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS achievements_list (
                id TEXT PRIMARY KEY,
                name TEXT,
                description TEXT,
                icon TEXT,
                reward REAL,
                requirement_type TEXT,
                requirement_value INTEGER
            )
        """)
        
        # Таблица промокодов
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS promocodes (
                code TEXT PRIMARY KEY,
                amount REAL,
                max_uses INTEGER DEFAULT 1,
                used_count INTEGER DEFAULT 0,
                expires_at TEXT,
                active INTEGER DEFAULT 1
            )
        """)
        self.add_column_if_not_exists("promocodes", "created_by", "INTEGER")
        self.add_column_if_not_exists("promocodes", "created_at", "TIMESTAMP")
        self.add_column_if_not_exists("promocodes", "deleted_by", "INTEGER")
        self.add_column_if_not_exists("promocodes", "deleted_at", "TIMESTAMP")
        self.add_column_if_not_exists("promocodes", "delete_reason", "TEXT")
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS promocode_uses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                promo_code TEXT,
                user_id INTEGER,
                used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Таблица логов платежей TON/USDT
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS ton_payments (
                tx_hash TEXT PRIMARY KEY,
                deal_id INTEGER,
                buyer_id INTEGER,
                currency TEXT,
                amount REAL,
                comment TEXT,
                source_address TEXT,
                confirmed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Таблица логов реферальных начислений с комиссии
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS referral_commission_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_user_id INTEGER,
                deal_id INTEGER,
                currency TEXT,
                commission_amount REAL,
                reward_amount REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Таблица активаций промокодов друзей (реферальные коды пользователей)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS friend_promo_activations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                user_id INTEGER,
                activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id)
            )
        """)

        # Таблица начислений 10% реферальных отчислений с пополнений
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS referral_deposit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                user_id INTEGER,
                currency TEXT,
                deposit_amount REAL,
                reward_amount REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Таблица служебного состояния мониторинга блокчейна
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS service_state (
                state_key TEXT PRIMARY KEY,
                state_value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Добавляем базовые достижения
        self.cursor.execute("DELETE FROM achievements_list")
        achievements = [
            ('first_deal', '🎯 Первая кровь', 'Первая завершённая сделка', '🎯', 50, 'deals_completed', 1),
            ('hot_ten', '🔥 Горячая десятка', '10 завершённых сделок', '🔥', 100, 'deals_completed', 10),
            ('diamond_trader', '💎 Алмазный трейдер', '50 завершённых сделок', '💎', 500, 'deals_completed', 50),
            ('legend', '👑 Легенда', '100 завершённых сделок', '👑', 1000, 'deals_completed', 100),
            ('referral_master', '📱 Мастер рефералов', '10 приглашённых друзей', '📱', 200, 'referrals', 10),
            ('premium_starter', '⭐ Премиум-старт', 'Первая покупка Premium', '⭐', 0, 'premium_purchase', 1),
            ('first_sale', '💰 Первый продавец', 'Первая сделка в роли продавца', '💰', 50, 'sales_count', 1),
            ('super_seller', '🚀 Супер-продавец', '50 сделок в роли продавца', '🚀', 500, 'sales_count', 50),
            ('active_user', '⚡ Активный пользователь', '20 завершённых сделок', '⚡', 200, 'deals_completed', 20),
            ('ton_holder', '💎 TON Холдер', 'Пополнил баланс в TON', '💎', 100, 'balance_ton', 100)
        ]
        
        for ach in achievements:
            self.cursor.execute("""
                INSERT OR IGNORE INTO achievements_list (id, name, description, icon, reward, requirement_type, requirement_value)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ach)
        
        # Таблица истории транзакций
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                currency TEXT DEFAULT 'RUB',
                type TEXT NOT NULL,
                description TEXT,
                related_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Таблица кодов авторизации
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS auth_codes (
                user_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.commit()
        logger.info("База данных инициализирована")

    def get_user(self, uid):
        self.cursor.execute("SELECT * FROM users WHERE user_id = ?", (uid,))
        return self.cursor.fetchone()

    def get_user_dict(self, uid):
        user = self.get_user(uid)
        if not user:
            return None
        col_names = [desc[0] for desc in self.cursor.description]
        return dict(zip(col_names, user))

    def reg_user(self, uid, name):
        if not self.get_user(uid):
            # Генерируем реферальный код
            ref_code = secrets.token_hex(4).upper()
            self.cursor.execute("""
                INSERT INTO users (user_id, username, referral_code) VALUES (?, ?, ?)
            """, (uid, name, ref_code))
            self.conn.commit()
        else:
            self.cursor.execute("UPDATE users SET last_activity = CURRENT_TIMESTAMP WHERE user_id = ?", (uid,))
            self.conn.commit()

    def get_referral_count(self, user_id: int) -> int:
        """Возвращает количество приглашённых пользователей"""
        self.cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
        return self.cursor.fetchone()[0]

    def get_referral_earnings(self, user_id: int) -> float:
        """Возвращает сумму заработка по рефералам"""
        self.cursor.execute("SELECT referral_earnings FROM users WHERE user_id = ?", (user_id,))
        result = self.cursor.fetchone()
        return result[0] if result else 0

    def get_user_by_referral_code(self, ref_code: str):
        self.cursor.execute("SELECT user_id FROM users WHERE referral_code = ?", (ref_code,))
        row = self.cursor.fetchone()
        return row[0] if row else None

    def upd_balance(self, uid, delta):
        self.cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (delta, uid))
        self.conn.commit()

    def get_balance(self, uid, currency):
        col_name = f"balance_{currency}"
        self.cursor.execute(f"SELECT {col_name} FROM users WHERE user_id = ?", (uid,))
        row = self.cursor.fetchone()
        return row[0] if row else 0

    def update_balance(self, uid, currency, delta):
        col_name = f"balance_{currency}"
        self.cursor.execute("BEGIN IMMEDIATE")
        try:
            self.cursor.execute(f"SELECT {col_name} FROM users WHERE user_id = ?", (uid,))
            current = self.cursor.fetchone()
            if current is None:
                self.conn.rollback()
                return False
            new_balance = (current[0] or 0) + delta
            if new_balance < 0:
                self.conn.rollback()
                return False
            self.cursor.execute(f"UPDATE users SET {col_name} = ? WHERE user_id = ?", (new_balance, uid))
            self.conn.commit()
            tx_type = 'deposit' if delta > 0 else 'withdrawal'
            self.add_transaction(uid, delta, currency, tx_type, f"{'Пополнение' if delta > 0 else 'Списание'} баланса")
            return True
        except Exception as e:
            self.conn.rollback()
            logger.error(f"update_balance error (uid={uid}, cur={currency}, delta={delta}): {e}")
            return False

    def get_all_balances(self, uid):
        balances = {}
        for curr in ["RUB", "BYN", "UAH", "KZT", "UZS", "EUR", "USD", "TON", "USDT", "STARS"]:
            balances[curr] = self.get_balance(uid, curr)
        return balances

    def set_card(self, uid, card, currency="RUB"):
        self.cursor.execute("UPDATE users SET card_details = ?, card_currency = ? WHERE user_id = ?", (card, currency, uid))
        self.conn.commit()

    def get_card_currency(self, uid):
        self.cursor.execute("SELECT card_currency FROM users WHERE user_id = ?", (uid,))
        row = self.cursor.fetchone()
        return row[0] if row else "RUB"

    def set_ton(self, uid, ton):
        self.cursor.execute("UPDATE users SET ton = ? WHERE user_id = ?", (ton, uid))
        self.conn.commit()

    def get_user_commission(self, user_id: int, action: str = "deal") -> float:
        is_premium = self.is_premium(user_id)
        if is_premium:
            return 0.0
        if action == "deal":
            return COMMISSION_DEAL
        else:
            return COMMISSION_WITHDRAW

    def create_deal(self, seller, item, amount, commission, deal_id, currency="RUB", payment_method="internal", payment_comment=None, payment_address=None, payment_amount=None):
        allowed = ['RUB', 'BYN', 'UAH', 'KZT', 'UZS', 'EUR', 'USD', 'TON', 'USDT', 'STARS']
        if currency not in allowed:
            currency = 'RUB'
        self.cursor.execute("""
            INSERT INTO deals (id, seller, item, amount, commission, currency, status, payment_method, payment_comment, payment_address, payment_amount)
            VALUES (?, ?, ?, ?, ?, ?, 'awaiting', ?, ?, ?, ?)
        """, (deal_id, seller, item, amount, commission, currency, payment_method, payment_comment, payment_address, payment_amount))
        self.conn.commit()
        return deal_id

    def get_deal(self, did):
        self.cursor.execute("SELECT * FROM deals WHERE id = ?", (did,))
        row = self.cursor.fetchone()
        if row:
            col_names = [desc[0] for desc in self.cursor.description]
            deal = dict(zip(col_names, row))
            deal.setdefault("currency", "RUB")
            deal.setdefault("status", "awaiting")
            return deal
        return None

    def upd_deal_status(self, did, status):
        if status == "completed":
            self.cursor.execute("UPDATE deals SET status = ?, completed = CURRENT_TIMESTAMP WHERE id = ?", (status, did))
        else:
            self.cursor.execute("UPDATE deals SET status = ? WHERE id = ?", (status, did))
        self.conn.commit()

    def set_buyer(self, did, buyer):
        self.cursor.execute("UPDATE deals SET buyer = ? WHERE id = ?", (buyer, did))
        self.conn.commit()

    def get_user_deals(self, uid):
        self.cursor.execute("SELECT * FROM deals WHERE seller = ? OR buyer = ? ORDER BY created DESC", (uid, uid))
        return self.cursor.fetchall()

    def get_active_deals(self):
        self.cursor.execute("SELECT * FROM deals WHERE status NOT IN ('completed', 'cancelled') ORDER BY created DESC")
        return self.cursor.fetchall()

    def get_all_users(self, limit=10, offset=0):
        self.cursor.execute("SELECT user_id, username, balance_RUB, is_premium FROM users ORDER BY user_id LIMIT ? OFFSET ?", (limit, offset))
        return self.cursor.fetchall()

    def get_users_count(self):
        self.cursor.execute("SELECT COUNT(*) FROM users")
        return self.cursor.fetchone()[0]

    def get_user_full(self, uid):
        self.cursor.execute("""
            SELECT u.*,
                COUNT(CASE WHEN d.seller = ? AND d.status = 'completed' THEN 1 END) as deals_completed,
                COUNT(CASE WHEN (d.seller = ? OR d.buyer = ?) AND d.status NOT IN ('completed', 'cancelled') THEN 1 END) as deals_active
            FROM users u
            LEFT JOIN deals d ON d.seller = u.user_id
            WHERE u.user_id = ?
            GROUP BY u.user_id
        """, (uid, uid, uid, uid))
        return self.cursor.fetchone()

    def get_premium_users(self):
        self.cursor.execute("""
            SELECT user_id, username, premium_until
            FROM users
            WHERE is_premium = 1 AND (premium_until > CURRENT_TIMESTAMP OR premium_until IS NULL)
        """)
        return self.cursor.fetchall()

    def is_premium(self, uid):
        self.cursor.execute("SELECT is_premium, premium_until FROM users WHERE user_id = ?", (uid,))
        row = self.cursor.fetchone()
        if row and row[0] == 1:
            if row[1] is None:
                return True
            try:
                # Поддерживаем оба формата даты
                expires_str = str(row[1])
                if '.' in expires_str:
                    expires = datetime.strptime(expires_str, '%Y-%m-%d %H:%M:%S.%f')
                else:
                    expires = datetime.strptime(expires_str, '%Y-%m-%d %H:%M:%S')
                if datetime.now() < expires:
                    return True
            except Exception as e:
                logger.warning(f"is_premium parse error for {uid}: {e}")
                return True  # Если не можем распарсить, считаем активным
        return False    

    def get_premium_info(self, user_id: int) -> dict:
        self.cursor.execute("""
            SELECT is_premium, premium_until, premium_granted_by, premium_granted_at, premium_duration_days
            FROM users WHERE user_id = ?
        """, (user_id,))
        row = self.cursor.fetchone()
        if row and row[0] == 1:
            expires = row[1]
            # Если есть микросекунды, обрезаем их
            if expires and expires != "FOREVER" and expires is not None:
                try:
                    # Если дата с микросекундами
                    if '.' in str(expires):
                        # Оставляем только до секунд
                        expires = str(expires).split('.')[0]
                except:
                    pass
            return {
                "active": True,
                "expires": expires,
                "granted_by": row[2],
                "granted_at": row[3],
                "duration_days": row[4]
            }
        return {"active": False}       

    def set_premium(self, user_id: int, days: int, granted_by: int):
        expires = datetime.now() + timedelta(days=days)
        self.cursor.execute("""
            UPDATE users SET
                is_premium = 1,
                premium_until = ?,
                premium_granted_by = ?,
                premium_granted_at = CURRENT_TIMESTAMP,
                premium_duration_days = ?
            WHERE user_id = ?
        """, (expires, granted_by, days, user_id))
        self.conn.commit()

    def remove_premium(self, user_id: int):
        self.cursor.execute("""
            UPDATE users SET
                is_premium = 0,
                premium_until = NULL,
                premium_granted_by = NULL,
                premium_granted_at = NULL,
                premium_duration_days = NULL
            WHERE user_id = ?
        """, (user_id,))
        self.conn.commit()

    def get_user_achievements(self, user_id: int) -> List[Tuple]:
        self.cursor.execute("""
            SELECT a.*, ua.earned_at, ua.claimed
            FROM achievements_list a
            LEFT JOIN user_achievements ua ON a.id = ua.achievement_id AND ua.user_id = ?
            ORDER BY ua.earned_at IS NULL, a.reward DESC
        """, (user_id,))
        return self.cursor.fetchall()

    def check_and_award_achievements(self, user_id: int, stats: dict):
        self.cursor.execute("SELECT id, requirement_type, requirement_value, reward FROM achievements_list")
        achievements = self.cursor.fetchall()
        earned = []
        for ach_id, req_type, req_value, reward in achievements:
            self.cursor.execute("SELECT id FROM user_achievements WHERE user_id = ? AND achievement_id = ?", (user_id, ach_id))
            if self.cursor.fetchone():
                continue
            achieved = False
            if req_type == 'deals_completed' and stats.get('completed_deals', 0) >= req_value:
                achieved = True
            elif req_type == 'referrals' and stats.get('referrals', 0) >= req_value:
                achieved = True
            elif req_type == 'sales_count' and stats.get('sales', 0) >= req_value:
                achieved = True
            elif req_type == 'balance_ton' and stats.get('ton_balance', 0) >= req_value:
                achieved = True
            elif req_type == 'premium_purchase' and stats.get('premium_count', 0) >= req_value:
                achieved = True
            if achieved:
                self.cursor.execute("""
                    INSERT INTO user_achievements (user_id, achievement_id) VALUES (?, ?)
                """, (user_id, ach_id))
                self.conn.commit()
                earned.append((ach_id, reward))
        return earned

    def claim_achievement_reward(self, user_id: int, achievement_id: str) -> float:
        self.cursor.execute("""
            SELECT reward, claimed FROM user_achievements ua
            JOIN achievements_list a ON ua.achievement_id = a.id
            WHERE ua.user_id = ? AND ua.achievement_id = ? AND ua.claimed = 0
        """, (user_id, achievement_id))
        result = self.cursor.fetchone()
        if result:
            reward = result[0]
            self.cursor.execute("""
                UPDATE user_achievements SET claimed = 1 WHERE user_id = ? AND achievement_id = ?
            """, (user_id, achievement_id))
            self.update_balance(user_id, "RUB", reward)
            self.conn.commit()
            return reward
        return 0

    def get_achievement_stats(self, user_id: int) -> dict:
        self.cursor.execute("SELECT COUNT(*) FROM deals WHERE (seller = ? OR buyer = ?) AND status = 'completed'", (user_id, user_id))
        completed_deals = self.cursor.fetchone()[0]
        self.cursor.execute("SELECT COUNT(*) FROM deals WHERE seller = ? AND status = 'completed'", (user_id,))
        sales = self.cursor.fetchone()[0]
        self.cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
        referrals = self.cursor.fetchone()[0]
        ton_balance = self.get_balance(user_id, 'TON')
        self.cursor.execute("SELECT COUNT(*) FROM user_achievements WHERE user_id = ? AND achievement_id = 'premium_starter'", (user_id,))
        premium_count = self.cursor.fetchone()[0]
        return {
            'completed_deals': completed_deals,
            'sales': sales,
            'referrals': referrals,
            'ton_balance': ton_balance,
            'premium_count': premium_count
        }

    def add_dispute(self, did, uid, reason):
        self.cursor.execute("INSERT INTO disputes (deal_id, opened_by, reason) VALUES (?, ?, ?)", (did, uid, reason))
        self.conn.commit()

    def get_disputes(self):
        self.cursor.execute("SELECT d.*, dl.item FROM disputes d JOIN deals dl ON d.deal_id = dl.id WHERE d.status = 'pending'")
        return self.cursor.fetchall()

    def resolve_dispute(self, did):
        self.cursor.execute("UPDATE disputes SET status = 'resolved' WHERE id = ?", (did,))
        self.conn.commit()

    def add_transaction(self, user_id: int, amount: float, currency: str, tx_type: str, description: str = None, related_id: int = None):
        self.cursor.execute(
            "INSERT INTO transactions (user_id, amount, currency, type, description, related_id) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, amount, currency, tx_type, description, related_id)
        )
        self.conn.commit()

    def add_notification(self, user_id: int, text: str):
        self.cursor.execute("INSERT INTO notifications (user_id, title, message) VALUES (?, ?, ?)", (user_id, "Уведомление", text))
        self.conn.commit()

    def get_unread(self, uid):
        self.cursor.execute("SELECT id, message FROM notifications WHERE user_id = ? AND is_read = 0", (uid,))
        return self.cursor.fetchall()

    def mark_read(self, uid):
        self.cursor.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (uid,))
        self.conn.commit()

    def get_all_users_for_mailing(self):
        self.cursor.execute("SELECT user_id FROM users WHERE notifications_enabled = 1 OR notifications_enabled IS NULL")
        return [row[0] for row in self.cursor.fetchall()]

    def create_promocode(self, code: str, amount: float, max_uses: int = 1, expires_days: int = 30, created_by: int = 0):
        expires = (datetime.now() + timedelta(days=expires_days)).strftime('%Y-%m-%d %H:%M:%S')
        self.cursor.execute("""
            INSERT OR REPLACE INTO promocodes (code, amount, max_uses, used_count, expires_at, active, created_by, created_at)
            VALUES (?, ?, ?, 0, ?, 1, ?, CURRENT_TIMESTAMP)
        """, (code.upper(), amount, max_uses, expires, created_by))
        self.conn.commit()

    def get_promocode(self, code: str):
        self.cursor.execute("SELECT * FROM promocodes WHERE code = ?", (code.upper(),))
        row = self.cursor.fetchone()
        if not row: return None
        col_names = [desc[0] for desc in self.cursor.description]
        return dict(zip(col_names, row))

    def use_promocode(self, code: str, user_id: int):
        # Атомарная транзакция — исключаем race conditions
        self.cursor.execute("BEGIN IMMEDIATE")
        try:
            promo = self.get_promocode(code)
            if not promo:
                self.conn.rollback()
                return False, 'Промокод не найден'
            if not promo['active']:
                self.conn.rollback()
                return False, 'Промокод неактивен'
            if promo['used_count'] >= promo['max_uses']:
                self.conn.rollback()
                return False, 'Промокод уже использован максимальное количество раз'
            if promo['expires_at']:
                try:
                    expires = datetime.strptime(promo['expires_at'], '%Y-%m-%d %H:%M:%S')
                    if datetime.now() > expires:
                        self.conn.rollback()
                        return False, 'Срок действия промокода истёк'
                except:
                    pass
            
            self.cursor.execute("SELECT id FROM promocode_uses WHERE promo_code = ? AND user_id = ?", (code.upper(), user_id))
            if self.cursor.fetchone():
                self.conn.rollback()
                return False, 'Вы уже активировали этот промокод'
            
            # Обновляем баланс
            col_name = "balance_RUB"
            self.cursor.execute(f"SELECT {col_name} FROM users WHERE user_id = ?", (user_id,))
            current = self.cursor.fetchone()
            if current is None:
                self.conn.rollback()
                return False, 'Пользователь не найден'
            new_balance = (current[0] or 0) + promo['amount']
            self.cursor.execute(f"UPDATE users SET {col_name} = ? WHERE user_id = ?", (new_balance, user_id))
            self.add_transaction(user_id, promo['amount'], 'RUB', 'promocode', f"Промокод {code}", related_id=0)
            
            # Начисляем 10% реферальных отчислений с пополнения по промокоду
            self.cursor.execute("SELECT referrer_id FROM friend_promo_activations WHERE user_id = ?", (user_id,))
            ref_row = self.cursor.fetchone()
            if ref_row:
                ref_id = ref_row[0]
                reward = quantize_amount(promo['amount'] * 0.10)
                if reward > 0:
                    self.cursor.execute(f"SELECT {col_name} FROM users WHERE user_id = ?", (ref_id,))
                    ref_cur = self.cursor.fetchone()
                    if ref_cur:
                        new_ref_bal = (ref_cur[0] or 0) + reward
                        self.cursor.execute(f"UPDATE users SET {col_name} = ? WHERE user_id = ?", (new_ref_bal, ref_id))
                        self.cursor.execute("""
                            INSERT INTO referral_deposit_log (referrer_id, user_id, currency, deposit_amount, reward_amount)
                            VALUES (?, ?, 'RUB', ?, ?)
                        """, (ref_id, user_id, promo['amount'], reward))
                        self.cursor.execute("INSERT INTO notifications (user_id, title, message) VALUES (?, ?, ?)",
                                            (ref_id, "Уведомление", f"💰 Реферальное отчисление: +{reward} RUB от пополнения пользователя."))
            
            # Увеличиваем счётчик использований
            self.cursor.execute("UPDATE promocodes SET used_count = used_count + 1 WHERE code = ?", (code.upper(),))
            # Записываем факт использования
            self.cursor.execute("INSERT INTO promocode_uses (promo_code, user_id) VALUES (?, ?)", (code.upper(), user_id))
            # Уведомление
            self.cursor.execute("INSERT INTO notifications (user_id, title, message) VALUES (?, ?, ?)",
                                (user_id, "Уведомление", f"🎉 Промокод {code} активирован! Получено {promo['amount']} RUB"))
            
            self.conn.commit()
            return True, promo['amount']
        except Exception as e:
            self.conn.rollback()
            logger.error(f"use_promocode error: {e}")
            return False, 'Ошибка при активации промокода. Попробуйте позже.'

    def get_all_promocodes(self):
        self.cursor.execute("SELECT * FROM promocodes ORDER BY code")
        return self.cursor.fetchall()

    def delete_promocode(self, code: str, deleted_by: int = 0, reason: str = "manual"):
        """Удаляет промокод, но сохраняет историю (деактивирует и помечает удалённым)"""
        self.cursor.execute("""
            UPDATE promocodes SET 
                active = 0,
                deleted_by = ?,
                deleted_at = CURRENT_TIMESTAMP,
                delete_reason = ?
            WHERE code = ?
        """, (deleted_by, reason, code.upper()))
        self.conn.commit()

    def hard_delete_promocode(self, code: str):
        """Полностью удаляет промокод из БД"""
        self.cursor.execute("DELETE FROM promocodes WHERE code = ?", (code.upper(),))
        self.conn.commit()

    def toggle_promocode(self, code: str):
        self.cursor.execute("UPDATE promocodes SET active = CASE WHEN active = 1 THEN 0 ELSE 1 END WHERE code = ?", (code.upper(),))
        self.conn.commit()

    def set_service_state(self, key: str, value: str):
        self.cursor.execute("""
            INSERT INTO service_state (state_key, state_value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(state_key) DO UPDATE SET state_value = excluded.state_value, updated_at = CURRENT_TIMESTAMP
        """, (key, value))
        self.conn.commit()

    def get_service_state(self, key: str, default: str = "") -> str:
        self.cursor.execute("SELECT state_value FROM service_state WHERE state_key = ?", (key,))
        row = self.cursor.fetchone()
        return row[0] if row else default

    def add_ton_payment(self, tx_hash: str, deal_id: int, buyer_id: int, currency: str, amount: float, comment: str, source_address: str):
        self.cursor.execute("""
            INSERT OR IGNORE INTO ton_payments (tx_hash, deal_id, buyer_id, currency, amount, comment, source_address)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (tx_hash, deal_id, buyer_id, currency, amount, comment, source_address))
        self.conn.commit()

    def has_ton_payment(self, tx_hash: str) -> bool:
        self.cursor.execute("SELECT 1 FROM ton_payments WHERE tx_hash = ?", (tx_hash,))
        return self.cursor.fetchone() is not None

    def mark_deal_paid_onchain(self, deal_id: int, tx_hash: str) -> bool:
        self.cursor.execute("""
            UPDATE deals
            SET status = 'paid', paid_tx_hash = ?, paid_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'payment_pending'
        """, (tx_hash, deal_id))
        changed = self.cursor.rowcount > 0
        self.conn.commit()
        return changed

    def start_external_payment(self, deal_id: int, buyer_id: int, comment: str, payment_address: str, payment_amount: float) -> bool:
        self.cursor.execute("""
            UPDATE deals
            SET buyer = COALESCE(buyer, ?),
                status = CASE WHEN status = 'awaiting' THEN 'payment_pending' ELSE status END,
                payment_comment = ?,
                payment_address = ?,
                payment_amount = ?
            WHERE id = ?
              AND status = 'awaiting'
              AND (buyer IS NULL OR buyer = ?)
        """, (buyer_id, comment, payment_address, payment_amount, deal_id, buyer_id))
        changed = self.cursor.rowcount > 0
        self.conn.commit()
        return changed

    def open_dispute(self, deal_id: int, opened_by: int, reason: str) -> bool:
        self.cursor.execute("SELECT status FROM deals WHERE id = ?", (deal_id,))
        row = self.cursor.fetchone()
        if not row or row[0] in ('completed', 'cancelled', 'disputed'):
            return False
        self.cursor.execute("UPDATE deals SET status = 'disputed', disputed_at = CURRENT_TIMESTAMP WHERE id = ?", (deal_id,))
        self.cursor.execute("INSERT INTO disputes (deal_id, opened_by, reason) VALUES (?, ?, ?)", (deal_id, opened_by, reason))
        self.conn.commit()
        return True

    def resolve_dispute_with_decision(self, dispute_id: int, decision: str) -> Optional[dict]:
        self.cursor.execute("SELECT deal_id FROM disputes WHERE id = ? AND status = 'pending'", (dispute_id,))
        row = self.cursor.fetchone()
        if not row:
            return None
        deal_id = row[0]
        deal = self.get_deal(deal_id)
        if not deal:
            return None

        amount = float(deal['amount'])
        currency = deal['currency']
        seller_id = deal['seller']
        buyer_id = deal.get('buyer')
        commission = self.get_user_commission(seller_id, 'deal')
        seller_amount = quantize_amount(amount - amount * commission)

        if decision == 'seller':
            self.update_balance(seller_id, currency, seller_amount)
            new_status = 'completed'
        elif decision == 'buyer' and buyer_id:
            self.update_balance(buyer_id, currency, amount)
            new_status = 'cancelled'
        else:
            return None

        self.cursor.execute("UPDATE deals SET status = ?, completed = CURRENT_TIMESTAMP WHERE id = ?", (new_status, deal_id))
        self.cursor.execute("UPDATE disputes SET status = 'resolved' WHERE id = ?", (dispute_id,))
        self.conn.commit()
        return {
            'deal_id': deal_id,
            'seller_id': seller_id,
            'buyer_id': buyer_id,
            'currency': currency,
            'amount': amount,
            'seller_amount': seller_amount,
            'decision': decision,
            'status': new_status
        }

    def create_promocode_with_expires(self, code: str, amount: float, max_uses: int, expires_at: str = None, created_by: int = 0):
        """Создаёт промокод с указанным сроком действия"""
        self.cursor.execute("""
            INSERT OR REPLACE INTO promocodes (code, amount, max_uses, used_count, expires_at, active, created_by, created_at)
            VALUES (?, ?, ?, 0, ?, 1, ?, CURRENT_TIMESTAMP)
        """, (code.upper(), amount, max_uses, expires_at, created_by))
        self.conn.commit()

    def credit_referral_commission(self, seller_id: int, deal_id: int, currency: str, commission_amount: float) -> Optional[dict]:
        self.cursor.execute("SELECT referred_by FROM users WHERE user_id = ?", (seller_id,))
        row = self.cursor.fetchone()
        referrer_id = row[0] if row else None
        if not referrer_id:
            return None

        self.cursor.execute("SELECT 1 FROM referral_commission_log WHERE deal_id = ?", (deal_id,))
        if self.cursor.fetchone():
            return None

        reward = quantize_amount(commission_amount * REFERRAL_COMMISSION_SHARE)
        if reward <= 0:
            return None

        self.cursor.execute("""
            INSERT INTO referral_commission_log (referrer_id, referred_user_id, deal_id, currency, commission_amount, reward_amount)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (referrer_id, seller_id, deal_id, currency, commission_amount, reward))
        self.cursor.execute("UPDATE users SET referral_earnings = COALESCE(referral_earnings, 0) + ? WHERE user_id = ?", (reward, referrer_id))
        self.conn.commit()
        return {'referrer_id': referrer_id, 'reward': reward, 'currency': currency, 'commission_amount': commission_amount}

    def get_user_by_friend_code(self, code: str):
        """Ищет пользователя по его реферальному коду (Промокод друга)"""
        self.cursor.execute("SELECT user_id, username FROM users WHERE referral_code = ?", (code.upper(),))
        row = self.cursor.fetchone()
        return {'user_id': row[0], 'username': row[1]} if row else None

    def use_friend_promocode(self, code: str, user_id: int):
        """Активация промокода друга (реферального кода пользователя)"""
        self.cursor.execute("BEGIN IMMEDIATE")
        try:
            # Проверяем, не активировал ли уже пользователь чей-то промокод
            self.cursor.execute("SELECT id FROM friend_promo_activations WHERE user_id = ?", (user_id,))
            if self.cursor.fetchone():
                self.conn.rollback()
                return False, 'Вы уже активировали промокод друга'

            referrer = self.get_user_by_friend_code(code)
            if not referrer:
                self.conn.rollback()
                return False, 'Промокод друга не найден'

            referrer_id = referrer['user_id']
            if referrer_id == user_id:
                self.conn.rollback()
                return False, 'Нельзя активировать свой собственный промокод'

            # Устанавливаем реферальную связь
            self.cursor.execute("UPDATE users SET referred_by = ? WHERE user_id = ?", (referrer_id, user_id))

            # Начисляем 50 RUB новому другу
            self.cursor.execute("SELECT balance_RUB FROM users WHERE user_id = ?", (user_id,))
            current = self.cursor.fetchone()
            if current is None:
                self.conn.rollback()
                return False, 'Пользователь не найден'
            new_bal = (current[0] or 0) + 50
            self.cursor.execute("UPDATE users SET balance_RUB = ? WHERE user_id = ?", (new_bal, user_id))

            # Начисляем 50 RUB пригласителю
            self.cursor.execute("SELECT balance_RUB FROM users WHERE user_id = ?", (referrer_id,))
            current_ref = self.cursor.fetchone()
            new_ref_bal = (current_ref[0] or 0) + 50
            self.cursor.execute("UPDATE users SET balance_RUB = ? WHERE user_id = ?", (new_ref_bal, referrer_id))

            # Записываем активацию
            self.cursor.execute("""
                INSERT INTO friend_promo_activations (referrer_id, user_id)
                VALUES (?, ?)
            """, (referrer_id, user_id))

            # Уведомления
            self.cursor.execute("INSERT INTO notifications (user_id, title, message) VALUES (?, ?, ?)",
                                (user_id, "Уведомление", f"🎉 Вы активировали промокод друга! Получено 50 RUB"))
            self.cursor.execute("INSERT INTO notifications (user_id, title, message) VALUES (?, ?, ?)",
                                (referrer_id, "Уведомление", f"🎉 Пользователь активировал ваш промокод! Вам начислено 50 RUB. Теперь вы получаете +10% с каждого его пополнения."))

            self.conn.commit()
            return True, {'referrer_id': referrer_id, 'bonus': 50, 'referrer_bonus': 50}
        except Exception as e:
            self.conn.rollback()
            logger.error(f"use_friend_promocode error: {e}")
            return False, 'Ошибка при активации промокода друга'

    def credit_referral_deposit_commission(self, user_id: int, currency: str, deposit_amount: float):
        """Начисляет 10% реферальных отчислений с пополнения другу, если пользователь активировал промокод друга"""
        self.cursor.execute("SELECT referrer_id FROM friend_promo_activations WHERE user_id = ?", (user_id,))
        row = self.cursor.fetchone()
        if not row:
            return None
        referrer_id = row[0]

        reward = quantize_amount(deposit_amount * 0.10)
        if reward <= 0:
            return None

        self.cursor.execute("BEGIN IMMEDIATE")
        try:
            col_name = f"balance_{currency}"
            self.cursor.execute(f"SELECT {col_name} FROM users WHERE user_id = ?", (referrer_id,))
            current = self.cursor.fetchone()
            if current is None:
                self.conn.rollback()
                return None
            new_bal = (current[0] or 0) + reward
            self.cursor.execute(f"UPDATE users SET {col_name} = ? WHERE user_id = ?", (new_bal, referrer_id))

            self.cursor.execute("""
                INSERT INTO referral_deposit_log (referrer_id, user_id, currency, deposit_amount, reward_amount)
                VALUES (?, ?, ?, ?, ?)
            """, (referrer_id, user_id, currency, deposit_amount, reward))

            self.cursor.execute("INSERT INTO notifications (user_id, title, message) VALUES (?, ?, ?)",
                                (referrer_id, "Уведомление", f"💰 Реферальное отчисление: +{reward} {currency} от пополнения пользователя."))

            self.conn.commit()
            return {'referrer_id': referrer_id, 'reward': reward, 'currency': currency}
        except Exception as e:
            self.conn.rollback()
            logger.error(f"credit_referral_deposit_commission error: {e}")
            return None

    def get_friend_promo_activations_count(self, user_id: int) -> int:
        """Сколько человек активировали промокод этого пользователя"""
        self.cursor.execute("SELECT COUNT(*) FROM friend_promo_activations WHERE referrer_id = ?", (user_id,))
        return self.cursor.fetchone()[0]

    def get_friend_promo_earnings(self, user_id: int) -> float:
        """Сколько заработано на реферальных отчислениях с пополнений"""
        self.cursor.execute("SELECT COALESCE(SUM(reward_amount), 0) FROM referral_deposit_log WHERE referrer_id = ?", (user_id,))
        return self.cursor.fetchone()[0]

    def get_stats(self):
        self.cursor.execute("SELECT COUNT(*) FROM users")
        users = self.cursor.fetchone()[0]
        balance = 0
        balance_detail = {}
        for curr in ["RUB", "BYN", "UAH", "KZT", "UZS", "EUR", "USD", "TON", "USDT", "STARS"]:
            self.cursor.execute(f"SELECT COALESCE(SUM(balance_{curr}), 0) FROM users")
            bal = self.cursor.fetchone()[0]
            balance_detail[curr] = bal
            balance += bal
        self.cursor.execute("SELECT COUNT(*) FROM deals WHERE status = 'completed'")
        completed = self.cursor.fetchone()[0]
        self.cursor.execute("SELECT COUNT(*) FROM deals WHERE status NOT IN ('completed', 'cancelled')")
        active = self.cursor.fetchone()[0]
        self.cursor.execute("SELECT COUNT(*) FROM disputes WHERE status = 'pending'")
        disputes = self.cursor.fetchone()[0]
        return {"users": users, "balance": balance, "balance_detail": balance_detail, "completed": completed, "active": active, "disputes": disputes}



db = Database()

# Default promocode
try:
    db.cursor.execute("SELECT code FROM promocodes WHERE code = 'NOVIX2026'")
    if not db.cursor.fetchone():
        db.create_promocode('NOVIX2026', 50, 1, 365)
except: pass

# ========== СОСТОЯНИЯ FSM ==========
class CreateDealState(StatesGroup):
    currency = State()
    name = State()
    price = State()

class BuyPremiumState(StatesGroup):
    currency = State()
    days = State()

class CardState(StatesGroup):
    waiting = State()

class TonState(StatesGroup):
    waiting = State()

class AdminCreditState(StatesGroup):
    uid = State()
    amount = State()

class AdminDebitState(StatesGroup):
    uid = State()
    amount = State()

class AdminMailingState(StatesGroup):
    title = State()
    text = State()

class AdminPremiumState(StatesGroup):
    user_id = State()
    days = State()

class AdminStates(StatesGroup):
    promo_code = State()          # Название промокода
    promo_amount = State()        # Сумма бонуса
    promo_type = State()          # Тип: ограниченный/бесконечный (limited/unlimited)
    promo_expires_type = State()  # Тип срока: дата/дни/бессрочный (date/days/forever)
    promo_expires_date = State()  # Конкретная дата (если выбран тип date)
    promo_expires_days = State()  # Количество дней (если выбран тип days)
    promo_max_uses = State()      # Максимальное количество использований (если ограниченный)

class PromoActivateState(StatesGroup):
    code = State()

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(Command("start"))
async def start_cmd(msg: types.Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    name = msg.from_user.username or "Unknown"

    args = msg.text.split()
    deal_id = None
    ref_code = None
    startapp_page = None
    
    if len(args) > 1:
        if args[1].startswith("deal_"):
            try:
                deal_id = int(args[1].replace("deal_", ""))
                logger.info(f"🔍 Переход по ссылке сделки: {deal_id}")
            except ValueError:
                logger.error(f"❌ Ошибка преобразования deal_id: {args[1]}")
        elif args[1].startswith("ref_"):
            ref_code = args[1].replace("ref_", "")
            logger.info(f"🔍 Переход по реферальной ссылке: {ref_code}")
        elif args[1] == "privacy":
            await msg.answer(
                "📄 *Политика конфиденциальности*\n\n"
                "Нажмите на кнопку ниже, чтобы открыть полный документ.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📄 Открыть политику", url=f"{WEBAPP_URL}/privacy.html")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")]
                ])
            )
            return
        elif args[1] == "terms":
            await msg.answer(
                "📜 *Пользовательское соглашение*\n\n"
                "Нажмите на кнопку ниже, чтобы открыть полный документ.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📜 Открыть соглашение", url=f"{WEBAPP_URL}/terms.html")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")]
                ])
            )
            return
        elif args[1].startswith("startapp_"):
            startapp_page = args[1].replace("startapp_", "")
            logger.info(f"🔍 Открытие Mini App: {startapp_page}")

    db.reg_user(uid, name)
    
    if ref_code:
        referrer_id = db.get_user_by_referral_code(ref_code)
        if referrer_id and referrer_id != uid:
            db.cursor.execute("UPDATE users SET referred_by = ? WHERE user_id = ?", (referrer_id, uid))
            db.conn.commit()
            db.update_balance(referrer_id, "RUB", 50)
            db.update_balance(uid, "RUB", 50)
            db.add_notification(referrer_id, f"🎉 Пользователь @{name} перешёл по вашей реферальной ссылке! Вам начислено 50 RUB")
            db.add_notification(uid, "🎉 Добро пожаловать! Вам начислено 50 RUB за регистрацию по реферальной ссылке")
            logger.info(f"Реферальный бонус: {referrer_id} -> {uid}")

    is_admin = uid in ADMIN_IDS

    if deal_id:
        deal = db.get_deal(deal_id)
        if deal and deal.get("status") == "awaiting":
            if deal["seller"] == uid:
                await msg.answer(
                    "❌ Вы не можете оплатить свою собственную сделку!",
                    reply_markup=main_kb(is_admin)
                )
                return
            
            commission = deal["commission"] if deal["commission"] else COMMISSION_DEAL
            currency = deal["currency"]
            
            text = (
                f"📦 *Сделка #{deal['id']}*\n\n"
                f"🎁 Товар: {escape_md(deal['item'])}\n"
                f"💰 Цена: {fmt_num(deal['amount'])} {currency}\n"
                f"💼 Комиссия: {int(commission*100)}%\n\n"
                f"Нажмите кнопку для оплаты"
            )
            await msg.answer(
                text,
                parse_mode="Markdown",
                reply_markup=deal_kb(deal['id'], "buyer", "awaiting")
            )
            return
        else:
            await msg.answer(
                "❌ Сделка не найдена или уже завершена.",
                reply_markup=main_kb(is_admin)
            )
            return

    if startapp_page:
        page_urls = {
            "home": f"{WEBAPP_URL}/index.html",
            "profile": f"{WEBAPP_URL}/profile.html",
            "deals": f"{WEBAPP_URL}/deals.html",
            "create_deal": f"{WEBAPP_URL}/create_deal.html",
            "premium": f"{WEBAPP_URL}/premium.html",
            "referral": f"{WEBAPP_URL}/referral.html",
            "buy_premium": f"{WEBAPP_URL}/buy_premium.html",
            "privacy": f"{WEBAPP_URL}/privacy.html",
            "terms": f"{WEBAPP_URL}/terms.html",
            "achievements": f"{WEBAPP_URL}/achievements.html"
        }
        
        web_app_url = page_urls.get(startapp_page, f"{WEBAPP_URL}/index.html")
        
        await msg.answer(
            "🚀 *Открываю Novix Gift Mini App...*\n\n"
            "📱 Здесь вы можете:\n"
            "• Создавать и оплачивать сделки\n"
            "• Покупать Premium подписку\n"
            "• Приглашать друзей и получать бонусы\n"
            "• Управлять своими реквизитами",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📱 Открыть Mini App", url=web_app_url)],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")]
            ])
        )
        return

    text = f"🎁 *Добро пожаловать в {BOT_NAME}!*\n\n✨ *Главное меню*"
    
    main_kb_with_app = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Открыть приложение", url=f"{WEBAPP_URL}/usersite/")],
        [InlineKeyboardButton(text="💰 Пополнить", callback_data="deposit"),
        InlineKeyboardButton(text="💸 Вывести", callback_data="withdraw")],
        [InlineKeyboardButton(text="💳 Карта", callback_data="set_card"),
        InlineKeyboardButton(text="📱 TON", callback_data="set_ton")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
        InlineKeyboardButton(text="⭐ Premium", callback_data="buy_premium")],
        [InlineKeyboardButton(text="➕ Создать сделку", callback_data="create_deal"),
        InlineKeyboardButton(text="📋 Мои сделки", callback_data="my_deals")],
        [InlineKeyboardButton(text="👥 Рефералы", callback_data="referral")]
    ])
    
    if is_admin:
        main_kb_with_app.inline_keyboard.append(
            [InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin_panel")]
        )
    
    if img_exists("ГЛАВНОЕ МЕНЮ.jpg"):
        await msg.answer_photo(
            photo=FSInputFile(img_path("ГЛАВНОЕ МЕНЮ.jpg")),
            caption=text,
            parse_mode="Markdown",
            reply_markup=main_kb_with_app
        )
    else:
        await msg.answer(
            text,
            parse_mode="Markdown",
            reply_markup=main_kb_with_app
        )

# ========== КОЛБЭКИ ОСНОВНЫХ КНОПОК ==========
@dp.callback_query(lambda c: c.data == "menu")
async def menu_cb(call: CallbackQuery, state: FSMContext = None):
    if state:
        await state.clear()
    uid = call.from_user.id
    is_admin = uid in ADMIN_IDS
    text = f"🎁 *{BOT_NAME}*\n\n✨ *Главное меню*"
    if img_exists("ГЛАВНОЕ МЕНЮ.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("ГЛАВНОЕ МЕНЮ.jpg")), caption=text, parse_mode="Markdown"),
            reply_markup=main_kb(is_admin)
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=main_kb(is_admin))
    await call.answer()


async def send_admin_menu(msg: types.Message):
    """Отправляет админ-панель как новое сообщение (используется из Message-хэндлеров)."""
    uid = msg.from_user.id
    is_admin = uid in ADMIN_IDS
    text = f"🎁 *{BOT_NAME}*\n\n✨ *Главное меню*"
    if img_exists("ГЛАВНОЕ МЕНЮ.jpg"):
        await msg.answer_photo(
            photo=FSInputFile(img_path("ГЛАВНОЕ МЕНЮ.jpg")),
            caption=text,
            parse_mode="Markdown",
            reply_markup=main_kb(is_admin)
        )
    else:
        await msg.answer(text, parse_mode="Markdown", reply_markup=main_kb(is_admin))


@dp.callback_query(lambda c: c.data == "cancel")
async def cancel_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await menu_cb(call)

@dp.callback_query(lambda c: c.data == "deposit")
async def deposit_cb(call: CallbackQuery):
    text = f"💳 *Пополнение баланса*\n\nДля пополнения напишите менеджеру:\n{MANAGER_USERNAME}\n\nУкажите ID: `{call.from_user.id}` и сумму."
    await edit_or_new(call, text, back_kb(), "ПОПОЛНИТЬ БАЛАНС.jpg")
    await call.answer()

@dp.callback_query(lambda c: c.data == "withdraw")
async def withdraw_cb(call: CallbackQuery):
    user_id = call.from_user.id
    is_premium = db.is_premium(user_id)
    commission = db.get_user_commission(user_id, "withdraw")
    
    # Получаем курсы валют
    try:
        rates = await currency_api.fetch_rates('RUB')
    except Exception:
        rates = {'USD': 73, 'EUR': 83, 'TON': 120, 'USDT': 73, 'STARS': 2, 
                 'UAH': 1.6, 'KZT': 0.15, 'UZS': 0.0061, 'BYN': 26}
    
    # === ИСПРАВЛЕНИЕ: пересчитываем все валюты в рубли ===
    total_balance_rub = 0
    balance_details = []
    
    for code in CURRENCIES.keys():
        bal = db.get_balance(user_id, code)
        if bal > 0:
            # Конвертируем в рубли
            if code == 'RUB':
                rub_value = bal
            else:
                rate = rates.get(code, 0)
                rub_value = bal * rate if rate > 0 else 0
            
            total_balance_rub += rub_value
            balance_details.append(f"• {fmt_num(bal)} {CURRENCIES[code]['symbol']} ({code}) ≈ {fmt_num(rub_value)} RUB")
    
    if total_balance_rub <= 0:
        text = "❌ *Недостаточно средств для вывода.*\n\nПополните баланс или дождитесь поступления средств по сделкам."
        if img_exists("ВЫВЕСТИ СРЕДСТВА.jpg"):
            await call.message.edit_media(
                InputMediaPhoto(media=FSInputFile(img_path("ВЫВЕСТИ СРЕДСТВА.jpg")), caption=text, parse_mode="Markdown"),
                reply_markup=back_kb()
            )
        else:
            await call.message.edit_text(text, parse_mode="Markdown", reply_markup=back_kb())
        await call.answer()
        return
    
    commission_text = "0%" if commission == 0 else f"{int(commission*100)}%"
    premium_text = "\n✨ *Premium*: комиссия 0%" if commission == 0 else ""
    
    # Собираем детали баланса
    balance_text = "\n".join(balance_details) if balance_details else "• 0 RUB"
    
    # Рассчитываем доступные суммы с учетом комиссии
    available_lines = []
    total_available_rub = 0
    for code in CURRENCIES.keys():
        bal = db.get_balance(user_id, code)
        if bal > 0:
            if code == 'RUB':
                rub_val = bal
            else:
                rate = rates.get(code, 0)
                rub_val = bal * rate if rate > 0 else 0
            
            if commission == 0:
                available_rub = rub_val
                available_lines.append(f"• {fmt_num(bal)} {CURRENCIES[code]['symbol']} ({code})")
            else:
                available_rub = rub_val * (1 - commission)
                available_with_commission = quantize_amount(bal * (1 - commission))
                available_lines.append(f"• {fmt_num(available_with_commission)} {CURRENCIES[code]['symbol']} ({code}) (из {fmt_num(bal)})")
            total_available_rub += available_rub
    
    total_available_rub = quantize_amount(total_available_rub)
    available_text = "\n".join(available_lines) if available_lines else "• 0"
    commission_note = f"\n💼 *Доступно к выводу (с учетом комиссии {commission_text}):*\n{available_text}" if commission > 0 else ""
    
    text = (
        f"💸 *Вывод средств*\n\n"
        f"💰 Ваш общий баланс: *{fmt_num(total_balance_rub)} RUB*\n"
        f"📊 Детализация:\n{balance_text}\n\n"
        f"💼 Комиссия: *{commission_text}*{premium_text}"
        f"{commission_note}\n\n"
        f"💵 *Итого к получению:* *{fmt_num(total_available_rub)} RUB*"
        f"{' (с учётом комиссии)' if commission > 0 else ''}\n\n"
        f"Для вывода средств напишите менеджеру:\n{MANAGER_USERNAME}\n\n"
        f"Укажите ID: `{user_id}`, сумму и реквизиты."
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📩 Написать менеджеру", url=f"https://t.me/{MANAGER_USERNAME.replace('@', '')}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu")]
    ])
    
    await edit_or_new(call, text, kb, "ВЫВЕСТИ СРЕДСТВА.jpg")
    await call.answer()

# ========== РЕФЕРАЛЫ ==========
@dp.callback_query(lambda c: c.data == "referral")
async def referral_cb(call: CallbackQuery):
    uid = call.from_user.id
    user = db.get_user_dict(uid)
    if not user:
        await call.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    ref_count = db.get_referral_count(uid)
    ref_earnings = db.get_referral_earnings(uid)
    ref_code = user.get('referral_code') or uid
    friend_activations = db.get_friend_promo_activations_count(uid)
    friend_earnings = db.get_friend_promo_earnings(uid)
    
    text = (f"👥 *Реферальная программа*\n\n"
            f"👤 Приглашено по ссылке: {ref_count} друзей\n"
            f"💰 Заработано: {ref_earnings} RUB\n"
            f"📱 Активаций промокода: {friend_activations}\n"
            f"💵 Отчислений с пополнений: {friend_earnings:.2f} RUB\n\n"
            f"🔗 Ваша ссылка:\n`https://t.me/NovixGift_Bot?start=ref_{ref_code}`\n\n"
            f"🎫 *Ваш промокод друга:* `{ref_code}`\n"
            f"Передайте его друзьям — они получат 50 RUB, а вы будете "
            f"получать +10% от каждого их пополнения!")
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔗 Создать приглашение", callback_data="ref_create_link")
    builder.button(text="📱 QR-код", callback_data="ref_qr")
    builder.button(text=f"💳 Вывести бонусы ({ref_earnings} ₽)", callback_data="ref_withdraw")
    builder.button(text="📋 Список рефералов", callback_data="ref_list")
    builder.button(text="📊 Аналитика", callback_data="ref_analytics")
    builder.button(text="🔙 В меню", callback_data="menu")
    builder.adjust(2)
    
    await edit_or_new(call, text, builder.as_markup(), "ПРИГЛАСИТЬ ДРУГА.jpg")
    await call.answer()

@dp.callback_query(lambda c: c.data == "ref_create_link")
async def ref_create_link_cb(call: CallbackQuery):
    uid = call.from_user.id
    user = db.get_user_dict(uid)
    code = user.get('referral_code') or uid
    link = f"https://t.me/NovixGift_Bot?start=ref_{code}"
    share_text = f"🎁 Присоединяйся к NovixGift! Переходи по моей ссылке и получи бонус: {link}"
    text = (f"🔗 *Ваша реферальная ссылка*\n\n"
            f"`{link}`\n\n"
            f"📤 Нажмите «Поделиться», чтобы отправить друзьям")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Поделиться", url=f"https://t.me/share/url?url={share_text}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="referral")]
    ])
    photo_path = img_path("ПРИГЛАСИТЬ ДРУГА.jpg")
    if img_exists("ПРИГЛАСИТЬ ДРУГА.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(photo_path), caption=text, parse_mode="Markdown"),
            reply_markup=kb
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data == "ref_qr")
async def ref_qr_cb(call: CallbackQuery):
    # Сразу отвечаем на callback, чтобы Telegram не выдавал таймаут
    await call.answer("🔄 Генерация QR-кода...", show_alert=False)
    
    uid = call.from_user.id
    user = db.get_user_dict(uid)
    code = user.get('referral_code') or uid
    link = f"https://t.me/NovixGift_Bot?start=ref_{code}"
    
    # Отправляем промежуточное сообщение и сохраняем его
    status_msg = await call.message.answer("⏳ *Генерация QR-кода, пожалуйста, подождите...*", parse_mode="Markdown")
    
    # Генерируем реальный QR-код
    try:
        qr = qrcode.QRCode(box_size=10, border=2)
        qr.add_data(link)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        
        photo = types.BufferedInputFile(buf.read(), filename="qrcode.png")
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Копировать ссылку", callback_data=f"copy_ref_{code}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="referral")]
        ])
        
        await status_msg.delete()
        
        await call.message.answer_photo(
            photo=photo,
            caption=f"📱 *Ваш QR-код приглашения*\n\nСсылка: `{link}`",
            parse_mode="Markdown",
            reply_markup=kb
        )
    except Exception as e:
        logger.error(f"QR generation error: {e}")
        await status_msg.delete()
        text = (f"📱 *QR-код приглашения*\n\n"
                f"Ссылка: `{link}`")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Копировать ссылку", callback_data=f"copy_ref_{code}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="referral")]
        ])
        await call.message.answer(text, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(lambda c: c.data == "ref_withdraw")
async def ref_withdraw_cb(call: CallbackQuery):
    uid = call.from_user.id
    earnings = db.get_referral_earnings(uid)
    if earnings <= 0:
        await call.answer("❌ На бонусном балансе нет средств!", show_alert=True)
        return
    db.cursor.execute("UPDATE users SET referral_earnings = 0 WHERE user_id = ?", (uid,))
    db.conn.commit()
    db.update_balance(uid, "RUB", earnings)
    db.add_notification(uid, f"💳 Бонусы {earnings} RUB выведены на RUB баланс")
    await call.answer(f"✅ {earnings} RUB выведены на ваш RUB баланс!", show_alert=True)
    # Возвращаемся к главному меню рефералов
    await referral_cb(call)

@dp.callback_query(lambda c: c.data == "ref_list")
async def ref_list_cb(call: CallbackQuery):
    uid = call.from_user.id
    db.cursor.execute("SELECT username, user_id FROM users WHERE referred_by = ? ORDER BY user_id DESC LIMIT 50", (uid,))
    referrals = db.cursor.fetchall()
    text = "📋 *Приглашённые друзья*\n\n"
    if referrals:
        for r in referrals[:20]:
            tag = f"@{r[0]}" if r[0] else f"ID {r[1]}"
            text += f"👤 {tag}\n"
    else:
        text += "Пока нет приглашённых друзей\n"
    text += f"\nВсего: {len(referrals)}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="referral")]])
    photo_path = img_path("ПРИГЛАСИТЬ ДРУГА.jpg")
    if img_exists("ПРИГЛАСИТЬ ДРУГА.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(photo_path), caption=text, parse_mode="Markdown"),
            reply_markup=kb
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data == "ref_analytics")
async def ref_analytics_cb(call: CallbackQuery):
    uid = call.from_user.id
    ref_count = db.get_referral_count(uid)
    earnings = db.get_referral_earnings(uid)
    db.cursor.execute("""
        SELECT COUNT(DISTINCT d.seller) FROM deals d 
        JOIN users u ON u.user_id = d.seller 
        WHERE u.referred_by = ? AND d.status = 'completed'
    """, (uid,))
    active = db.cursor.fetchone()[0] or 0
    text = (f"📊 *Реферальная аналитика*\n\n"
            f"👥 Приглашено: {ref_count}\n"
            f"✅ Активных: {active}\n"
            f"💰 Заработано: {earnings} RUB\n"
            f"📈 Конверсия: {round(active/ref_count*100, 1) if ref_count > 0 else 0}%")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="referral")]])
    photo_path = img_path("ПРИГЛАСИТЬ ДРУГА.jpg")
    if img_exists("ПРИГЛАСИТЬ ДРУГА.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(photo_path), caption=text, parse_mode="Markdown"),
            reply_markup=kb
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    await call.answer()

@dp.callback_query(lambda c: c.data.startswith("copy_ref_"))
async def copy_ref_cb(call: CallbackQuery):
    code = call.data.replace("copy_ref_", "")
    link = f"https://t.me/NovixGift_Bot?start=ref_{code}"
    await call.answer(f"🔗 Ссылка скопирована!", show_alert=True)

@dp.message(lambda m: m.text and m.text.startswith('/code'))
async def code_cmd(msg: types.Message):
    uid = msg.from_user.id
    conn = sqlite3.connect("novixgift.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT code FROM auth_codes WHERE user_id=? AND expires_at > datetime('now') ORDER BY created_at DESC LIMIT 1",
        (uid,)
    )
    row = cur.fetchone()
    conn.close()
    if row:
        await msg.answer(f"🔐 Ваш код для входа на сайт: <b>{row['code']}</b>\n\nДействителен 5 минут.")
    else:
        await msg.answer("❌ У вас нет активного кода. Запросите новый на сайте в разделе «Вход».")

@dp.message(lambda m: m.text and m.text.startswith('/promo'))
async def activate_promo_cmd(msg: types.Message):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("📝 Используйте: /promo КОД\n\nНапример: /promo NOVIX2026")
        return
    code = parts[1].strip().upper()
    
    # Сначала пробуем промокод друга
    async with db_batch_lock:
        friend_result = db.use_friend_promocode(code, msg.from_user.id)
    if friend_result[0]:
        data = friend_result[1]
        await msg.answer(
            f"✅ *Промокод друга активирован!*\n\n"
            f"💰 Вам начислено: {data['bonus']} RUB\n"
            f"👤 Пригласитель получил: {data['referrer_bonus']} RUB",
            parse_mode="Markdown"
        )
        return
    
    success, result = db.use_promocode(code, msg.from_user.id)
    if success:
        await msg.answer(f"✅ Промокод {code} активирован!\n💰 Начислено: {result} RUB")
    else:
        await msg.answer(f"❌ {result}")

@dp.callback_query(lambda c: c.data == "activate_promo")
async def activate_promo_cb(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(PromoActivateState.code)
    text = "🎫 *Активация промокода*\n\nВведите код промокода:\n\nНапример: `NOVIX2026`"
    if img_exists("ПРОМОКОДЫ.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("ПРОМОКОДЫ.jpg")), caption=text, parse_mode="Markdown"),
            reply_markup=cancel_kb()
        )
    else:
        await call.message.delete()
        await call.message.answer(text, parse_mode="Markdown", reply_markup=cancel_kb())


@dp.message(PromoActivateState.code)
async def activate_promo_code_msg(msg: types.Message, state: FSMContext):
    code = msg.text.strip().upper()
    
    # Сначала пробуем активировать как промокод друга (реферальный код пользователя)
    async with db_batch_lock:
        friend_result = db.use_friend_promocode(code, msg.from_user.id)
    
    if friend_result[0]:
        data = friend_result[1]
        await state.clear()
        await msg.answer(
            f"✅ *Промокод друга активирован!*\n\n"
            f"💰 Вам начислено: {data['bonus']} RUB\n"
            f"👤 Пригласитель получил: {data['referrer_bonus']} RUB\n\n"
            f"✨ Теперь пригласитель будет получать +10% от каждого вашего пополнения!",
            parse_mode="Markdown",
            reply_markup=back_kb()
        )
        return
    
    # Если не промокод друга, пробуем административный промокод
    async with db_batch_lock:
        success, result = db.use_promocode(code, msg.from_user.id)
    
    await state.clear()
    
    if success:
        await msg.answer(
            f"✅ *Промокод активирован!*\n\n💰 Начислено: {result} RUB",
            parse_mode="Markdown",
            reply_markup=back_kb()
        )
    else:
        await msg.answer(
            f"❌ {result}",
            parse_mode="Markdown",
            reply_markup=back_kb()
        )

# ========== КАРТА ==========
@dp.callback_query(lambda c: c.data == "set_card")
async def set_card_cb(call: CallbackQuery, state: FSMContext):
    text = "💳 *Моя карта для вывода*\n\nВыберите валюту карты (RUB / BYN / UAH / KZT / EUR):"
    if img_exists("КАРТА ДЛЯ ВЫВОДА.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("КАРТА ДЛЯ ВЫВОДА.jpg")), caption=text, parse_mode="Markdown"),
            reply_markup=card_currency_kb()
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=card_currency_kb())
    await call.answer()

@dp.callback_query(lambda c: c.data.startswith("card_cur_"))
async def set_card_currency_cb(call: CallbackQuery, state: FSMContext):
    currency = call.data.replace("card_cur_", "")
    await state.update_data(card_currency=currency)
    text = f"💳 *Введите номер карты*\n\nВалюта карты: {currency}\n\nПример: 4276 1600 1234 5678"
    if img_exists("КАРТА ДЛЯ ВЫВОДА.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("КАРТА ДЛЯ ВЫВОДА.jpg")), caption=text, parse_mode="Markdown"),
            reply_markup=cancel_kb()
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=cancel_kb())
    await state.set_state(CardState.waiting)
    await call.answer()

@dp.message(CardState.waiting)
async def set_card_msg(msg: types.Message, state: FSMContext):
    card = msg.text.replace(" ", "")
    if not card.isdigit() or len(card) not in [16, 19]:
        await msg.answer("❌ Неверный формат. Введите 16 цифр", reply_markup=cancel_kb())
        return
    
    data = await state.get_data()
    currency = data.get('card_currency', 'RUB')
    db.set_card(msg.from_user.id, card, currency)
    await state.clear()
    await msg.answer(
        f"✅ *Карта сохранена!*\n\n💳 {card}\n🌍 Валюта: {currency}",
        parse_mode="Markdown",
        reply_markup=back_kb()
    )

# ========== TON КОШЕЛЕК ==========
@dp.callback_query(lambda c: c.data == "set_ton")
async def set_ton_cb(call: CallbackQuery, state: FSMContext):
    text = "💎 *Введите адрес TON-кошелька*\n\nПример: `UQCD39VS5jcptHL8vMjEXrzGaRcCVYtoq7BGPk2vwUCGzE`"
    if img_exists("TON.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("TON.jpg")), caption=text, parse_mode="Markdown"),
            reply_markup=cancel_kb()
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=cancel_kb())
    await state.set_state(TonState.waiting)
    await call.answer()

@dp.message(TonState.waiting)
async def set_ton_msg(msg: types.Message, state: FSMContext):
    ton = msg.text.strip()
    if not ton.startswith("UQ") and not ton.startswith("EQ"):
        await msg.answer("❌ Неверный формат. Адрес начинается с UQ или EQ", reply_markup=cancel_kb())
        return
    db.set_ton(msg.from_user.id, ton)
    await state.clear()
    await msg.answer("✅ *TON кошелек сохранен!*", parse_mode="Markdown", reply_markup=back_kb())

@dp.callback_query(lambda c: c.data == "profile")
async def profile_cb(call: CallbackQuery):
    user = db.get_user_dict(call.from_user.id)
    if not user:
        await call.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    ton = user.get('ton') or user.get('ton_wallet') or "не указан"
    card = user.get('card_details') or "не указана"
    
    is_premium = db.is_premium(call.from_user.id)
    premium_info = db.get_premium_info(call.from_user.id)
    
    deals = db.get_user_deals(call.from_user.id)
    completed_deals = len([d for d in deals if (d[7] if len(d) > 7 else "") == "completed"]) if deals else 0
    
    role = get_user_role(completed_deals)
    
    balances = ""
    for code, info in CURRENCIES.items():
        bal = db.get_balance(call.from_user.id, code)
        if bal > 0:
            balances += f"• {fmt_num(bal)} {info['symbol']} ({code})\n"
    
    if not balances:
        balances = "• 0 🇷🇺 (RUB)\n"
    
    # ===== ПАРСИМ ДАТУ С МИКРОСЕКУНДАМИ =====
    if is_premium and premium_info.get("active"):
        expires = premium_info.get("expires", "")
        if expires and expires != "FOREVER" and expires is not None:
            try:
                # Если дата с микросекундами
                if '.' in str(expires):
                    exp_date = datetime.strptime(str(expires), '%Y-%m-%d %H:%M:%S.%f').strftime('%d.%m.%Y')
                else:
                    exp_date = datetime.strptime(str(expires), '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y')
                premium_status = f"✅ Активен до {exp_date}"
            except Exception as e:
                premium_status = f"✅ Активен до {expires}"
        else:
            premium_status = "✅ Активен (FOREVER)"
        
        # === ИСПРАВЛЕНИЕ: показываем username создателя ===
        granted_by = premium_info.get("granted_by")
        if granted_by and granted_by != 0:
            creator = db.get_user_dict(granted_by)
            if creator and creator.get('username'):
                premium_status += f"\n👑 Выдал: @{creator.get('username')}"
            else:
                premium_status += f"\n👑 Выдал: `{granted_by}`"
    else:
        premium_status = "❌ Не активен"
    
    is_admin = call.from_user.id in ADMIN_IDS
    admin_badge = "👑 *Администратор*\n" if is_admin else ""
    
    text = (
        f"👤 *Личный кабинет*\n\n"
        f"{admin_badge}"
        f"🆔 Ваш ID: `{user['user_id']}`\n"
        f"🔗 Username: @{escape_md(user['username'] or 'без username')}\n"
        f"🏆 Роль: {role}\n"
        f"📊 Сделок завершено: {completed_deals}\n\n"
        f"💼 *Баланс:*\n{balances}\n"
        f"💎 TON-кошелёк: {escape_md(ton)}\n\n"
        f"💳 Карта для вывода:\n{escape_md(card)}\n\n"
        f"⭐ *Premium статус:* {premium_status}"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🎫 Активировать промокод", callback_data="activate_promo")
    builder.button(text="🔙 В меню", callback_data="menu")
    builder.adjust(1)
    
    await edit_or_new(call, text, builder.as_markup(), "ЛИЧНЫЙ КАБИНЕТ.jpg")
    await call.answer()

# ========== PREMIUM ==========
@dp.callback_query(lambda c: c.data == "buy_premium")
async def buy_premium_cb(call: CallbackQuery, state: FSMContext):
    text = "⭐ *Premium подписка*\n\nВыберите валюту для оплаты:"
    if img_exists("PREMIUM ПОДПИСКА.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("PREMIUM ПОДПИСКА.jpg")), caption=text, parse_mode="Markdown"),
            reply_markup=premium_currency_kb()
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=premium_currency_kb())
    await state.set_state(BuyPremiumState.currency)
    await call.answer()

@dp.callback_query(BuyPremiumState.currency)
async def premium_currency_cb(call: CallbackQuery, state: FSMContext):
    if not call.data.startswith("premium_cur_"):
        return
    
    currency = call.data.replace("premium_cur_", "")
    await state.update_data(currency=currency)
    
    # Используем единые курсы
    rates = PREMIUM_RATES
    price_rub = PREMIUM_PRICES_RUB
    
    text = f"⭐ *Premium подписка*\n\n💰 Валюта оплаты: {currency}\n\nВыберите длительность:"
    kb = []
    
    for days, rub_price in price_rub.items():
        rate = rates.get(currency, 1)
        converted = int(rub_price / rate) if rate > 0 else rub_price
        label = "👑 FOREVER" if days == 365 else f"📅 {days} дней"
        kb.append([InlineKeyboardButton(
            text=f"{label} - {converted} {currency}",
            callback_data=f"premium_buy_{days}_{currency}"
        )])
    
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="buy_premium")])
    kb.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")])
    
    if img_exists("PREMIUM ПОДПИСКА.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("PREMIUM ПОДПИСКА.jpg")), caption=text, parse_mode="Markdown"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(BuyPremiumState.days)
    await call.answer()

@dp.callback_query(BuyPremiumState.days)
async def premium_buy_cb(call: CallbackQuery, state: FSMContext):
    if not call.data.startswith("premium_buy_"):
        return
    
    parts = call.data.split("_")
    days = int(parts[2])
    currency = parts[3]
    
    rates = PREMIUM_RATES
    price_rub = PREMIUM_PRICES_RUB
    
    rate = rates.get(currency, 1)
    if rate <= 0:
        await call.answer(f"❌ Неизвестная валюта: {currency}", show_alert=True)
        return
    
    if currency not in rates:
        await call.answer(f"❌ Валюта {currency} не поддерживается", show_alert=True)
        return
    
    uid = call.from_user.id
    price_in_currency = int(price_rub[days] / rate)
    user_balance = db.get_balance(uid, currency)
    
    days_label = "FOREVER" if days == 365 else f"{days} дней"
    
    # Если достаточно средств в выбранной валюте — оплачиваем напрямую
    if user_balance >= price_in_currency:
        async with db_batch_lock:
            if db.update_balance(uid, currency, -price_in_currency):
                db.set_premium(uid, days, 0)
        
        text_success = (
            f"✅ *Premium подписка активирована!*\n\n"
            f"📅 Длительность: {days_label}\n"
            f"💰 Оплачено: {price_in_currency} {currency}\n"
            f"✨ Комиссия при сделках и выводе: *0%*"
        )
        if img_exists("PREMIUM ПОДПИСКА.jpg"):
            await call.message.edit_media(
                InputMediaPhoto(media=FSInputFile(img_path("PREMIUM ПОДПИСКА.jpg")), caption=text_success, parse_mode="Markdown"),
                reply_markup=main_kb(uid in ADMIN_IDS)
            )
        else:
            await call.message.edit_text(text_success, parse_mode="Markdown", reply_markup=main_kb(uid in ADMIN_IDS))
        await state.clear()
        await call.answer()
        return
    
    # Недостаточно средств в выбранной валюте — предлагаем "Общий счёт"
    price_rub_value = price_rub[days]
    
    # Собираем все балансы пользователя
    all_balances = {}
    total_rub = 0
    for curr in rates.keys():
        bal = db.get_balance(uid, curr)
        all_balances[curr] = bal
        if bal > 0:
            curr_rate = rates.get(curr, 0)
            if curr_rate > 0:
                total_rub += bal * curr_rate if curr != "RUB" else bal
    
    if total_rub < price_rub_value:
        await call.answer(
            f"❌ Недостаточно средств!\n\n"
            f"💰 Нужно: {price_in_currency} {currency} (~{price_rub_value} RUB)\n"
            f"💼 Ваш общий баланс: ~{fmt_num(total_rub)} RUB\n\n"
            f"Пополните баланс и попробуйте снова.",
            show_alert=True
        )
        return
    
    # Составляем детализацию общего счета
    deduction_plan = {}
    remaining_rub = price_rub_value
    
    deduction_order = ["RUB", "USD", "EUR", "USDT", "TON", "STARS", "BYN", "UAH", "KZT", "UZS"]
    for curr in deduction_order:
        if remaining_rub <= 0:
            break
        bal = all_balances.get(curr, 0)
        if bal <= 0:
            continue
        if curr == "RUB":
            deduct = min(bal, remaining_rub)
            deduction_plan[curr] = deduct
            remaining_rub -= deduct
        else:
            curr_rate = rates.get(curr, 0)
            if curr_rate <= 0:
                continue
            needed_in_curr = remaining_rub / curr_rate
            if bal >= needed_in_curr:
                deduction_plan[curr] = round(needed_in_curr, 6)
                remaining_rub = 0
            else:
                deduction_plan[curr] = bal
                remaining_rub -= bal * curr_rate
    
    if remaining_rub > 0:
        await call.answer("❌ Не удалось составить план списания. Попробуйте другую валюту.", show_alert=True)
        return
    
    # Строим текст детализации
    plan_parts = []
    for curr, amount in deduction_plan.items():
        plan_parts.append(f"{fmt_num(amount)} {CURRENCIES.get(curr, {}).get('symbol', curr)} ({curr})")
    plan_str = " + ".join(plan_parts)
    
    await state.update_data(
        premium_days=days,
        premium_price_rub=price_rub_value,
        deduction_plan=deduction_plan
    )
    
    text = (
        f"⭐ *Premium подписка*\n\n"
        f"Вы выбрали оплату в *{currency}*.\n"
        f"На вашем балансе недостаточно {currency}, "
        f"но мы можем списать средства с ваших совокупных активов.\n\n"
        f"💳 *Списать:* {plan_str}\n"
        f"💰 *Эквивалент:* {price_rub_value} RUB\n"
        f"📅 *Длительность:* {days_label}\n\n"
        f"Подтверждаете списание?"
    )
    
    # Добавляем название дня в callback_data
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, оплатить с общего счета", callback_data=f"premium_confirm_cross_{days}")],
        [InlineKeyboardButton(text="❌ Нет, отмена", callback_data="cancel")]
    ])
    
    if img_exists("PREMIUM ПОДПИСКА.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("PREMIUM ПОДПИСКА.jpg")), caption=text, parse_mode="Markdown"),
            reply_markup=kb
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    await call.answer()


@dp.callback_query(lambda c: c.data.startswith("premium_confirm_cross_"))
async def premium_confirm_cross_cb(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    data = await state.get_data()
    
    days = data.get("premium_days")
    price_rub_value = data.get("premium_price_rub")
    deduction_plan = data.get("deduction_plan")
    
    if not all([days, price_rub_value, deduction_plan]):
        await call.answer("❌ Сессия истекла. Начните заново.", show_alert=True)
        await state.clear()
        return
    
    # Атомарное списание с общего счета
    async with db_batch_lock:
        success = True
        deducted_summary = {}
        for curr, amount in deduction_plan.items():
            if not db.update_balance(uid, curr, -amount):
                success = False
                break
            deducted_summary[curr] = amount
        
        if not success:
            # Откатываем уже списанное
            for curr, amount in deducted_summary.items():
                db.update_balance(uid, curr, amount)
            await call.answer("❌ Ошибка при списании средств. Транзакция отменена.", show_alert=True)
            await state.clear()
            return
        
        db.set_premium(uid, days, 0)
    
    days_label = "FOREVER" if days == 365 else f"{days} дней"
    plan_parts = []
    for curr, amount in deduction_plan.items():
        plan_parts.append(f"{fmt_num(amount)} {curr}")
    
    text_success = (
        f"✅ *Premium подписка активирована!*\n\n"
        f"📅 Длительность: {days_label}\n"
        f"💰 Списано с общего счета: {' + '.join(plan_parts)} (эквивалент {price_rub_value} RUB)\n"
        f"✨ Комиссия при сделках и выводе: *0%*"
    )
    if img_exists("PREMIUM ПОДПИСКА.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("PREMIUM ПОДПИСКА.jpg")), caption=text_success, parse_mode="Markdown"),
            reply_markup=main_kb(uid in ADMIN_IDS)
        )
    else:
        await call.message.edit_text(text_success, parse_mode="Markdown", reply_markup=main_kb(uid in ADMIN_IDS))
    
    await state.clear()
    await call.answer()

# ========== СОЗДАНИЕ СДЕЛКИ ==========
@dp.callback_query(lambda c: c.data == "create_deal")
async def create_deal_cb(call: CallbackQuery, state: FSMContext):
    text = "🌍 *Выберите валюту для сделки:*\n\nRUB, USD, EUR, UAH, KZT, UZS, BYN, TON, USDT, Stars"
    if img_exists("СОЗДАНИЕ СДЕЛКИ.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("СОЗДАНИЕ СДЕЛКИ.jpg")), caption=text, parse_mode="Markdown"),
            reply_markup=currency_kb()
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=currency_kb())
    await state.set_state(CreateDealState.currency)
    await call.answer()

@dp.callback_query(CreateDealState.currency)
async def deal_currency_cb(call: CallbackQuery, state: FSMContext):
    if not call.data.startswith("cur_"):
        return
    
    currency = call.data.replace("cur_", "")
    await state.update_data(currency=currency)
    await state.set_state(CreateDealState.name)
    
    text = f"💱 *Валюта выбрана: {currency}*\n\n📦 *Введите название товара:*"
    if img_exists("СОЗДАНИЕ СДЕЛКИ.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("СОЗДАНИЕ СДЕЛКИ.jpg")), caption=text, parse_mode="Markdown"),
            reply_markup=cancel_kb()
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=cancel_kb())
    await call.answer()

@dp.message(CreateDealState.name)
async def deal_name_msg(msg: types.Message, state: FSMContext):
    if msg.text == "❌ Отмена":
        await state.clear()
        await menu_cb(msg)
        return
    
    if not msg.text or len(msg.text.strip()) < 1:
        await msg.answer("❌ Название не может быть пустым. Введите название товара:", reply_markup=cancel_kb())
        return
    
    await state.update_data(name=msg.text.strip())
    await state.set_state(CreateDealState.price)
    
    text = "💰 *Введите цену* (целое число, например: 1000):"
    
    if img_exists("СОЗДАНИЕ СДЕЛКИ.jpg"):
        await msg.answer_photo(
            photo=FSInputFile(img_path("СОЗДАНИЕ СДЕЛКИ.jpg")),
            caption=text,
            parse_mode="Markdown",
            reply_markup=cancel_kb()
        )
    else:
        await msg.answer(text, parse_mode="Markdown", reply_markup=cancel_kb())

@dp.message(CreateDealState.price)
async def deal_price_msg(msg: types.Message, state: FSMContext):
    if msg.text == "❌ Отмена":
        await state.clear()
        await menu_cb(msg)
        return
    
    try:
        price = int(msg.text.strip())
        if price <= 0:
            await msg.answer("❌ Цена должна быть больше 0. Введите целое число:", reply_markup=cancel_kb())
            return
    except ValueError:
        await msg.answer("❌ Это не число! Введите целое число (например: 1000):", reply_markup=cancel_kb())
        return
    
    data = await state.get_data()
    item = data.get('name')
    currency = data.get('currency', 'RUB')
    
    if not item:
        await msg.answer("❌ Ошибка: название товара не найдено. Начните заново.", reply_markup=main_kb(msg.from_user.id in ADMIN_IDS))
        await state.clear()
        return
    
    import random
    deal_id = random.randint(100000, 999999)
    while db.get_deal(deal_id):
        deal_id = random.randint(100000, 999999)
    
    commission = db.get_user_commission(msg.from_user.id, "deal")
    db.create_deal(msg.from_user.id, item, price, commission, deal_id, currency)
    
    link = f"https://t.me/{BOT_USERNAME}?start=deal_{deal_id}"
    
    text = (
        f"✅ *Сделка успешно создана!*\n\n"
        f"🧾 ID: `{deal_id}`\n"
        f"📦 Товар: {escape_md(item)}\n"
        f"💰 Цена: {fmt_num(price)} {currency}\n"
        f"💼 Комиссия: {int(commission*100)}%\n\n"
        f"🔗 Отправьте покупателю ссылку:\n`{link}`\n\n"
        f"Или нажмите *Поделиться сделкой* ниже."
    )
    
    await state.clear()
    
    if img_exists("СДЕЛКА УСПЕШНО СОЗДАНА.jpg"):
        await msg.answer_photo(
            photo=FSInputFile(img_path("СДЕЛКА УСПЕШНО СОЗДАНА.jpg")),
            caption=text,
            parse_mode="Markdown",
            reply_markup=share_kb(deal_id)
        )
    else:
        await msg.answer(text, parse_mode="Markdown", reply_markup=share_kb(deal_id))

# ========== МОИ СДЕЛКИ ==========
@dp.callback_query(lambda c: c.data == "my_deals")
async def my_deals_cb(call: CallbackQuery):
    uid = call.from_user.id
    deals = db.get_user_deals(uid)
    
    if not deals:
        text = "📭 У вас пока нет сделок"
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=back_kb())
        await call.answer()
        return
    
    text = "📋 *Мои сделки*\n\nВыберите сделку для просмотра деталей:"
    kb = []
    
    for d in deals[:20]:
        deal_id = d[0]
        item_name = d[3][:25] if len(str(d[3])) > 25 else d[3]
        is_seller = (d[1] == uid)
        role_emoji = "🟢" if is_seller else "🔵"
        text_label = f"{role_emoji} №{deal_id} — {escape_md(str(item_name))}"
        kb.append([InlineKeyboardButton(text=text_label, callback_data=f"deal_detail_{deal_id}")])
    
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="menu")])
    
    if img_exists("МОИ СДЕЛКИ.jpg"):
        photo_path = img_path("МОИ СДЕЛКИ.jpg")
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(photo_path), caption=text, parse_mode="Markdown"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()


@dp.callback_query(lambda c: c.data.startswith("deal_detail_"))
async def deal_detail_cb(call: CallbackQuery):
    deal_id = int(call.data.replace("deal_detail_", ""))
    deal = db.get_deal(deal_id)
    
    if not deal:
        await call.answer("❌ Сделка не найдена", show_alert=True)
        return
    
    uid = call.from_user.id
    
    # Получаем информацию об участниках
    seller_info = db.get_user_dict(deal['seller'])
    seller_name = f"@{seller_info['username']}" if seller_info and seller_info.get('username') else f"ID {deal['seller']}"
    
    buyer_id = deal.get('buyer')
    if buyer_id:
        buyer_info = db.get_user_dict(buyer_id)
        buyer_name = f"@{buyer_info['username']}" if buyer_info and buyer_info.get('username') else f"ID {buyer_id}"
    else:
        buyer_name = "❌ Не назначен"
    
    role = "Продавец" if deal['seller'] == uid else "Покупатель" if buyer_id == uid else "Наблюдатель"
    
    status_emojis = {
        "awaiting": "⏳ Ожидает оплаты",
        "payment_pending": "💳 Ожидание on-chain платежа",
        "paid": "💰 Оплачено",
        "item_sent": "📦 Товар отправлен",
        "completed": "✅ Завершена",
        "cancelled": "❌ Отменена",
        "disputed": "⚖️ Спор"
    }
    status_text = status_emojis.get(deal.get('status', ''), deal.get('status', 'Неизвестно'))
    
    created = deal.get('created', 'Неизвестно')
    completed_ts = deal.get('completed', '')
    
    payment_method = deal.get('payment_method', 'internal')
    method_text = "💳 Внутренний баланс" if payment_method == 'internal' else "🔗 Блокчейн (TON/USDT)"
    
    currency = deal.get('currency', 'RUB')
    amount = float(deal['amount'])
    commission_rate = float(deal.get('commission', 0))
    
    # Конвертация в RUB если валюта не RUB
    rub_conversion = ""
    if currency != "RUB":
        try:
            rates = await currency_api.fetch_rates('RUB')
        except Exception:
            rates = {'USD': 73, 'EUR': 83, 'TON': 120, 'USDT': 73, 'STARS': 2,
                     'UAH': 1.6, 'KZT': 0.15, 'UZS': 0.0061, 'BYN': 26}
        rate = rates.get(currency, 0)
        if rate > 0:
            rub_value = quantize_amount(amount * rate)
            rub_conversion = f"\n💵 ~{fmt_num(rub_value)} RUB"
    
    # Расчет чистой прибыли продавца
    seller_profit_line = ""
    if deal['seller'] == uid:
        if db.is_premium(uid):
            net_amount = amount
            comm_note = "0% (Premium)"
        else:
            net_amount = quantize_amount(amount * (1 - commission_rate))
            comm_note = f"{int(commission_rate * 100)}%"
        seller_profit_line = f"\n💰 *Ваш чистый заработок:* {fmt_num(net_amount)} {currency} (комиссия {comm_note})"
    
    text = (
        f"📄 *Сделка №{deal_id}*\n\n"
        f"👤 *Роль:* {role}\n"
        f"🆔 Продавец: {escape_md(str(seller_name))} (`{deal['seller']}`)\n"
        f"🆔 Покупатель: {escape_md(str(buyer_name))}\n\n"
        f"🎁 *Товар:* {escape_md(str(deal['item']))}\n"
        f"💰 *Сумма:* {fmt_num(amount)} {currency}{rub_conversion}\n"
        f"💼 *Комиссия:* {int(commission_rate * 100)}%\n"
        f"💳 *Способ оплаты:* {method_text}\n"
        f"{seller_profit_line}\n"
        f"📌 *Статус:* {status_text}\n"
        f"📅 *Создана:* {created[:16] if created != 'Неизвестно' else created}\n"
    )
    if completed_ts:
        text += f"✅ *Завершена:* {completed_ts[:16]}\n"
    
    # Информация об on-chain платеже
    if deal.get('payment_comment'):
        text += f"🔗 Комментарий: `{deal['payment_comment']}`\n"
    if deal.get('paid_tx_hash'):
        text += f"📝 Tx Hash: `{deal['paid_tx_hash'][:16]}...`\n"
    
    # Кнопки в зависимости от роли и статуса + кнопка "Назад" к списку сделок
    deal_kb_markup = deal_kb(deal_id, "buyer" if buyer_id == uid else "seller", deal.get('status', 'awaiting'))
    deal_kb_markup.inline_keyboard.insert(0, [
        InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="my_deals")
    ])
    
    await edit_or_new(call, text, deal_kb_markup, "МОИ СДЕЛКИ.jpg")
    await call.answer()

# ========== ОПЛАТА СДЕЛКИ ==========
@dp.callback_query(lambda c: c.data.startswith("pay_"))
async def pay_cb(call: CallbackQuery):
    did = int(call.data[4:])
    buyer = call.from_user.id

    async with deal_lock:  # CHANGED: защита от race condition при оплате сделки
        deal = db.get_deal(did)
        if not deal or deal.get("status") not in ("awaiting", "payment_pending"):
            await call.answer("❌ Сделка недоступна для оплаты", show_alert=True)
            return

        seller = deal["seller"]
        if buyer == seller:
            await call.answer("❌ Вы не можете оплатить свою собственную сделку!", show_alert=True)
            return

        if deal.get("buyer") and deal["buyer"] != buyer:
            await call.answer("❌ Сделка уже закреплена за другим покупателем", show_alert=True)
            return

        currency = deal["currency"]
        amount = float(deal["amount"])
        payment_method = deal.get("payment_method") or ("ton" if currency in ("TON", "USDT") else "internal")

        if payment_method == "ton":  # CHANGED: on-chain flow для TON/USDT
            payment_comment = deal.get("payment_comment") or generate_payment_comment(did, buyer)
            payment_address = deal.get("payment_address") or TON_ESCROW_ADDRESS
            if not payment_address:
                await call.answer("❌ TON escrow-адрес не настроен в backend", show_alert=True)
                return

            payment_amount = quantize_amount(deal.get("payment_amount") or amount, "0.000001")
            started = db.start_external_payment(did, buyer, payment_comment, payment_address, payment_amount)
            if not started:
                deal = db.get_deal(did)
                if deal and deal.get("status") == "payment_pending" and deal.get("buyer") == buyer:
                    pass
                else:
                    await call.answer("❌ Не удалось зафиксировать on-chain оплату", show_alert=True)
                    return

            text = (
                f"💎 *Оплата сделки #{did}*\n\n"
                f"Отправьте *{payment_amount} {currency}* на адрес:\n`{escape_md(payment_address)}`\n\n"
                f"Комментарий / payload:\n`{payment_comment}`\n\n"
                f"После входящей транзакции сделка автоматически перейдёт в статус *Оплачено*."
            )
            await call.message.edit_text(text, parse_mode="Markdown", reply_markup=deal_kb(did, "buyer", "payment_pending"))
            await call.answer("⏳ Ожидаем входящую транзакцию", show_alert=True)
            return

        buyer_balance = db.get_balance(buyer, currency)
        if buyer_balance < amount:
            await call.answer(f"❌ Недостаточно средств. Баланс: {fmt_num(buyer_balance)} {currency}", show_alert=True)
            return

        if not deal.get("buyer"):
            db.set_buyer(did, buyer)
        db.update_balance(buyer, currency, -amount)
        db.upd_deal_status(did, "paid")

    await call.answer("✅ Оплата прошла успешно!", show_alert=True)

    text = f"✅ *Сделка #{did} оплачена!*\n\nОжидайте передачи товара от продавца"
    if img_exists("ВАША СДЕЛКА БЫЛА ОПЛАЧЕНА.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("ВАША СДЕЛКА БЫЛА ОПЛАЧЕНА.jpg")), caption=text, parse_mode="Markdown")
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown")

    await notify_user(
        seller,
        f"💰 *Сделка #{did} оплачена!*\n\n"
        f"🎁 {escape_md(deal['item'])}\n"
        f"💰 {fmt_num(amount)} {currency}\n\n"
        f"Покупатель внёс средства. Передайте товар.",
        reply_markup=deal_kb(did, "seller", "paid")
    )

async def get_premium_rates() -> dict:
    """Получает актуальные курсы для Premium"""
    try:
        rates = await currency_api.fetch_rates('RUB')
        # Оставляем только нужные валюты
        return {k: rates.get(k, PREMIUM_RATES.get(k, 1)) for k in PREMIUM_RATES.keys()}
    except Exception:
        return PREMIUM_RATES

# ========== ПЕРЕДАЧА ТОВАРА ==========
@dp.callback_query(lambda c: c.data.startswith("sent_"))
async def sent_cb(call: CallbackQuery):
    did = int(call.data[5:])

    async with deal_lock:
        deal = db.get_deal(did)
        if not deal or deal.get("status") != "paid":
            await call.answer("❌ Сделка не оплачена", show_alert=True)
            return

        if deal["seller"] != call.from_user.id:
            await call.answer("❌ Вы не продавец", show_alert=True)
            return

        db.upd_deal_status(did, "item_sent")

    await call.answer("✅ Товар передан!", show_alert=True)
    await call.message.edit_text(f"✅ *Товар передан!*\nОжидайте подтверждения", parse_mode="Markdown")

    await notify_user(
        deal["buyer"],
        f"📦 *Товар передан!*\n\nСделка #{did}\n✅ Подтвердите получение",
        reply_markup=deal_kb(did, "buyer", "item_sent")
    )

# ========== ПОДТВЕРЖДЕНИЕ ПОЛУЧЕНИЯ ==========
@dp.callback_query(lambda c: c.data.startswith("recv_"))
async def receive_cb(call: CallbackQuery):
    did = int(call.data[5:])

    async with deal_lock:  # CHANGED: атомарное завершение сделки и начисление продавцу
        deal = db.get_deal(did)
        if not deal or deal.get("status") != "item_sent":
            await call.answer("❌ Товар не передан", show_alert=True)
            return

        if deal["buyer"] != call.from_user.id:
            await call.answer("❌ Вы не покупатель", show_alert=True)
            return

        seller_id = deal["seller"]
        amount = float(deal["amount"])
        currency = deal["currency"]

        commission = db.get_user_commission(seller_id, "deal")
        commission_amount = quantize_amount(amount * commission)
        seller_amount = quantize_amount(amount - commission_amount)

        db.update_balance(seller_id, currency, seller_amount)
        db.upd_deal_status(did, "completed")
        referral_bonus = db.credit_referral_commission(seller_id, did, currency, commission_amount)

    await call.answer("✅ Сделка завершена!", show_alert=True)
    await call.message.edit_text(f"✅ *Сделка #{did} завершена!*\nСпасибо!", parse_mode="Markdown")

    premium_text = " (без комиссии)" if commission == 0 else ""
    await notify_user(
        seller_id,
        f"✅ *Сделка #{did} завершена!*\n\n"
        f"💰 Получено: {fmt_num(seller_amount)} {currency}{premium_text}\n"
        f"🟢 Статус сделки обновлён: успешно завершена."
    )
    await notify_user(
        call.from_user.id,
        f"✅ *Сделка #{did} успешно завершена!*\n\nСредства переведены продавцу."
    )

    if referral_bonus:
        await notify_user(
            referral_bonus['referrer_id'],
            f"👥 *Реферальный бонус*\n\n"
            f"По сделке #{did} начислено {referral_bonus['reward']} RUB "
            f"(10% от сервисной комиссии)."
        )

# ========== СПОРЫ ==========
@dp.callback_query(lambda c: c.data.startswith("dispute_"))
async def dispute_cb(call: CallbackQuery, state: FSMContext):
    did = int(call.data[8:])
    deal = db.get_deal(did)
    
    if not deal:
        await call.answer("❌ Сделка не найдена", show_alert=True)
        return
    
    await state.update_data(deal_id=did)
    text = "⚠️ *Причина спора*\n\nНапишите причину (макс 500 символов):"
    photo_path = img_path("СДЕЛКА УСПЕШНО СОЗДАНА.jpg")
    if img_exists("СДЕЛКА УСПЕШНО СОЗДАНА.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(photo_path), caption=text, parse_mode="Markdown"),
            reply_markup=cancel_kb()
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=cancel_kb())
    await state.set_state("waiting_dispute")
    await call.answer()

@dp.message(StateFilter("waiting_dispute"))
async def dispute_msg(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    deal_id = data.get('deal_id')

    if not deal_id:
        await msg.answer("❌ Ошибка: сделка не найдена", reply_markup=back_kb())
        await state.clear()
        return

    reason = (msg.text or "").strip()[:500]
    if not reason:
        await msg.answer("❌ Укажите причину спора", reply_markup=cancel_kb())
        return

    async with deal_lock:
        deal = db.get_deal(deal_id)
        if not deal or msg.from_user.id not in [deal.get('seller'), deal.get('buyer')]:
            await msg.answer("❌ Вы не участник этой сделки", reply_markup=back_kb())
            await state.clear()
            return

        opened = db.open_dispute(deal_id, msg.from_user.id, reason)
        if not opened:
            await msg.answer("❌ Спор уже открыт или сделка недоступна", reply_markup=back_kb())
            await state.clear()
            return

        db.cursor.execute("SELECT last_insert_rowid()")
        dispute_id = db.cursor.fetchone()[0]

    admin_kb_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выплатить продавцу", callback_data=f"dispute_decide_seller_{dispute_id}"),
         InlineKeyboardButton(text="↩️ Вернуть покупателю", callback_data=f"dispute_decide_buyer_{dispute_id}")]
    ])

    for admin in ADMIN_IDS:
        await notify_user(
            admin,
            f"⚠️ *Новый спор*\n\nСделка #{deal_id}\nОткрыл: `{msg.from_user.id}`\nПричина: {escape_md(reason)}",
            reply_markup=admin_kb_markup
        )

    counterpart_id = deal['buyer'] if msg.from_user.id == deal['seller'] else deal['seller']
    if counterpart_id:
        await notify_user(counterpart_id, f"⚖️ *По сделке #{deal_id} открыт спор.*\n\nСредства временно заморожены.")

    await state.clear()
    await msg.answer(
        "✅ *Спор открыт!*\nАдминистратор рассмотрит его, пока сделка заморожена.",
        parse_mode="Markdown",
        reply_markup=back_kb()
    )

# ========== ДОСТИЖЕНИЯ (BOT) ==========
@dp.callback_query(lambda c: c.data == "achievements")
async def achievements_cb(call: CallbackQuery):
    user_id = call.from_user.id
    achievements = db.get_user_achievements(user_id)
    stats = db.get_achievement_stats(user_id)
    
    earned = db.check_and_award_achievements(user_id, stats)
    for ach_id, reward in earned:
        await call.answer(f"🎉 Получено достижение! +{reward} RUB", show_alert=True)
    
    achievements = db.get_user_achievements(user_id)
    
    text = "🏆 *Мои достижения*\n\n"
    text += f"📊 *Статистика:*\n"
    text += f"• Завершённых сделок: {stats['completed_deals']}\n"
    text += f"• Продаж: {stats['sales']}\n"
    text += f"• Приглашённых друзей: {stats['referrals']}\n\n"
    
    text += "✨ *Достижения:*\n"
    for ach in achievements:
        name = ach[1]
        desc = ach[2]
        icon = ach[3]
        reward = ach[4]
        earned_at = ach[8]
        claimed = ach[9]
        
        if earned_at:
            if claimed:
                text += f"✅ {icon} *{name}* — получено (+{reward} RUB)\n"
            else:
                text += f"🎁 {icon} *{name}* — доступно! /claim_{ach[0]}\n"
        else:
            text += f"🔒 {icon} *{name}* — {desc} (+{reward} RUB)\n"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")]
    ])
    
    await edit_or_new(call, text, keyboard)
    await call.answer()

@dp.message(lambda m: m.text and m.text.startswith("/claim_"))
async def claim_achievement(msg: types.Message):
    ach_id = msg.text.replace("/claim_", "")
    reward = db.claim_achievement_reward(msg.from_user.id, ach_id)
    
    if reward > 0:
        await msg.answer(f"🎉 Поздравляем! Вы получили {reward} RUB за достижение!")
    else:
        await msg.answer("❌ Награда уже получена или недоступна")

# ========== API ДЛЯ ДОСТИЖЕНИЙ ==========
async def handle_achievements(request):
    auth_user = await get_authenticated_webapp_user(request)
    if not auth_user:
        return JSONResponse(content={'error': 'Unauthorized Mini App request'}, status_code=401)

    user_id = int(request.query_params.get('user_id', 0))
    if auth_user.get('id') != user_id:
        return JSONResponse(content={'error': 'User mismatch'}, status_code=403)

    achievements = db.get_user_achievements(user_id)
    stats = db.get_achievement_stats(user_id)
    
    return JSONResponse(content={
        'stats': stats,
        'achievements': [{
            'id': a[0],
            'name': a[1],
            'description': a[2],
            'icon': a[3],
            'reward': a[4],
            'earned_at': a[8],
            'claimed': a[9]
        } for a in achievements]
    })

async def handle_claim_achievement(request):
    try:
        auth_user = await get_authenticated_webapp_user(request)
        if not auth_user:
            return JSONResponse(content={'success': False, 'error': 'Unauthorized'}, status_code=401)

        data = await request.json()
        user_id = int(data.get('user_id', 0))
        if auth_user.get('id') != user_id:
            return JSONResponse(content={'success': False, 'error': 'User mismatch'}, status_code=403)

        achievement_id = data.get('achievement_id')
        if not achievement_id:
            return JSONResponse(content={'success': False, 'error': 'Missing params'}, status_code=400)
        
        reward = db.claim_achievement_reward(user_id, achievement_id)
        if reward > 0:
            return JSONResponse(content={'success': True, 'reward': reward})
        return JSONResponse(content={'success': False, 'error': 'Already claimed or not available'})
    except Exception as e:
        return JSONResponse(content={'success': False, 'error': str(e)}, status_code=500)

# ========== АДМИН-ПАНЕЛЬ ==========
@dp.callback_query(lambda c: c.data == "admin_panel")
async def admin_panel_cb(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    text = "⚙️ *Админ-панель*"
    if img_exists("ПАНЕЛЬ АДМИНИСТРАТОРА.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("ПАНЕЛЬ АДМИНИСТРАТОРА.jpg")), caption=text, parse_mode="Markdown"),
            reply_markup=admin_kb()
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=admin_kb())
    await call.answer()

user_page = {}

@dp.callback_query(lambda c: c.data in [
    "admin_credit", "admin_debit", "admin_premium", "admin_premium_users",
    "admin_mailing", "admin_users", "admin_deals", "admin_disputes",
    "admin_stats", "admin_promocodes", "admin_close"
])
async def admin_actions_cb(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("⛔ Доступ запрещен", show_alert=True)
        return

    action = call.data[6:]

    if action == "credit":
        if img_exists("ЗАЧИСЛИТЬ.jpg"):
            await call.message.edit_media(
                InputMediaPhoto(media=FSInputFile(img_path("ЗАЧИСЛИТЬ.jpg")), caption="💰 *Введите ID пользователя для зачисления:*", parse_mode="Markdown"),
                reply_markup=admin_cancel_kb()
            )
        else:
            await call.message.edit_text("💰 *Введите ID пользователя для зачисления:*", parse_mode="Markdown", reply_markup=admin_cancel_kb())
        await state.set_state(AdminCreditState.uid)
    elif action == "debit":
        if img_exists("СПИСАТЬ.jpg"):
            await call.message.edit_media(
                InputMediaPhoto(media=FSInputFile(img_path("СПИСАТЬ.jpg")), caption="💸 *Введите ID пользователя для списания:*", parse_mode="Markdown"),
                reply_markup=admin_cancel_kb()
            )
        else:
            await call.message.edit_text("💸 *Введите ID пользователя для списания:*", parse_mode="Markdown", reply_markup=admin_cancel_kb())
        await state.set_state(AdminDebitState.uid)
    elif action == "premium":
        if img_exists("PREMIUM ПОДПИСКА.jpg"):
            await call.message.edit_media(
                InputMediaPhoto(media=FSInputFile(img_path("PREMIUM ПОДПИСКА.jpg")), caption="👑 *Введите ID пользователя для выдачи Premium:*", parse_mode="Markdown"),
                reply_markup=admin_cancel_kb()
            )
        else:
            await call.message.edit_text("👑 *Введите ID пользователя для выдачи Premium:*", parse_mode="Markdown", reply_markup=admin_cancel_kb())
        await state.set_state(AdminPremiumState.user_id)
    elif action == "premium_users":
        users = db.get_premium_users()
        if not users:
            text = "👑 Нет активных Premium подписок"
        else:
            text = "👑 *Активные Premium подписки*\n\n"
            for u in users:
                expires = u[2] if u[2] else "FOREVER"
                if expires != "FOREVER":
                    expires = expires[:10]
                text += f"🆔 {u[0]} | @{escape_md(u[1] or '?')} | до {expires}\n"
        if img_exists("PREMIUM ПОДПИСКА.jpg"):
            photo_path = img_path("PREMIUM ПОДПИСКА.jpg")
            await call.message.edit_media(
                InputMediaPhoto(media=FSInputFile(photo_path), caption=text, parse_mode="Markdown"),
                reply_markup=admin_kb()
            )
        else:
            await call.message.edit_text(text, parse_mode="Markdown", reply_markup=admin_kb())
    elif action == "mailing":
        await state.set_state(AdminMailingState.title)
        
        # Получаем количество пользователей
        users = db.get_all_users_for_mailing()
        total_users = len(users)
        
        text = (
            f"📨 *Рассылка по вашему боту*\n\n"
            f"👥 Получателей: *{total_users}* человек\n\n"
            f"Отправьте сообщение — оно уйдёт всем пользователям вашего бота "
            f"с сохранением форматирования, фото/видео и премиум-эмодзи."
        )
        # Создаём клавиатуру с кнопкой отмены, ведущей в админ-панель
        mailing_cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")]
        ])
        if img_exists("РАССЫЛКА.jpg"):
            await call.message.edit_media(
                InputMediaPhoto(media=FSInputFile(img_path("РАССЫЛКА.jpg")), caption=text, parse_mode="Markdown"),
                reply_markup=mailing_cancel_kb
            )
        else:
            await call.message.edit_text(text, parse_mode="Markdown", reply_markup=mailing_cancel_kb)
    elif action == "users":
        user_page[call.from_user.id] = 0
        await show_users_page(call, 0)
    elif action == "deals":
        deals = db.get_active_deals()
        if not deals:
            text = "📭 Нет активных сделок"
        else:
            text = "📦 *Активные сделки*\n\n"
            for d in deals:
                status = d[7] if len(d) > 7 else "awaiting"
                currency = d[6] if len(d) > 6 else "RUB"
                emoji = {"awaiting": "⏳", "paid": "💰", "item_sent": "📦"}.get(status, "❓")
                text += f"{emoji} #{d[0]} | {escape_md(d[3][:20])} | {fmt_num(d[4])} {currency} | {status}\n"
        photo_path = img_path("АКТИВНЫЕ СДЕЛКИ.jpg")
        if img_exists("АКТИВНЫЕ СДЕЛКИ.jpg"):
            await call.message.edit_media(
                InputMediaPhoto(media=FSInputFile(photo_path), caption=text, parse_mode="Markdown"),
                reply_markup=admin_kb()
            )
        else:
            await call.message.edit_text(text, parse_mode="Markdown", reply_markup=admin_kb())
    elif action == "disputes":
        disputes = db.get_disputes()
        if not disputes:
            text = "⚠️ Нет открытых споров"
        else:
            text = "⚠️ *Споры*\n\n"
            for d in disputes:
                text += f"📝 Спор #{d[0]} | Сделка #{d[1]} | {escape_md(d[3][:50])}\n✅ /resolve_{d[0]}\n\n"
        photo_path = img_path("СПОРЫ.jpg")
        if img_exists("СПОРЫ.jpg"):
            await call.message.edit_media(
                InputMediaPhoto(media=FSInputFile(photo_path), caption=text, parse_mode="Markdown"),
                reply_markup=admin_kb()
            )
        else:
            await call.message.edit_text(text, parse_mode="Markdown", reply_markup=admin_kb())
    elif action == "stats":
        s = db.get_stats()
        try:
            rates = await currency_api.fetch_rates('RUB')
        except Exception:
            rates = {'USD': 73, 'EUR': 83, 'TON': 120, 'USDT': 73, 'STARS': 2,
                     'UAH': 1.6, 'KZT': 0.15, 'UZS': 0.0061, 'BYN': 26}
        
        detail_lines = []
        total_rub = 0
        for curr in ["RUB", "BYN", "UAH", "KZT", "UZS", "EUR", "USD", "TON", "USDT", "STARS"]:
            bal = s['balance_detail'].get(curr, 0)
            if bal > 0:
                if curr == 'RUB':
                    rub_val = bal
                else:
                    rate = rates.get(curr, 0)
                    rub_val = bal * rate if rate > 0 else 0
                total_rub += rub_val
                info = CURRENCIES.get(curr, {"symbol": curr})
                detail_lines.append(f"• {fmt_num(bal)} {info['symbol']} ({curr}) ≈ {fmt_num(rub_val)} RUB")
        
        detail_text = "\n".join(detail_lines) if detail_lines else "• Нет средств\n"
        
        text = (
            f"📊 *Статистика бота*\n\n"
            f"👥 Пользователей: *{s['users']}*\n"
            f"✅ Завершённых сделок: *{s['completed']}*\n"
            f"🔄 Активных сделок: *{s['active']}*\n"
            f"⚠️ Споров: *{s['disputes']}*\n\n"
            f"💰 *Балансы по валютам:*\n{detail_text}\n"
            f"💵 *Итого:* {fmt_num(total_rub)} RUB"
        )
        photo_path = img_path("СТАТИСТИКА.jpg")
        if img_exists("СТАТИСТИКА.jpg"):
            await call.message.edit_media(
                InputMediaPhoto(media=FSInputFile(photo_path), caption=text, parse_mode="Markdown"),
                reply_markup=admin_kb()
            )
        else:
            await call.message.edit_text(text, parse_mode="Markdown", reply_markup=admin_kb())
    elif action == "promocodes":
        await admin_promocodes_cb(call)
        return
    elif action == "close":
        await menu_cb(call)
        return

    await call.answer()

async def show_users_page(call: CallbackQuery, page: int):
    per_page = 5
    total = db.get_users_count()
    offset = page * per_page
    users = db.get_all_users(per_page, offset)
    total_pages = (total + per_page - 1) // per_page

    if not users:
        await call.message.delete()
        await call.message.answer("👥 Нет пользователей", reply_markup=admin_kb())
        return

    text = f"👥 *Пользователи (стр. {page + 1}/{total_pages})*\n\n"
    for u in users:
        premium = "💎" if u[3] else ""
        text += f"🆔 {u[0]} | @{escape_md(u[1] or '?')} {premium} | 💰 {fmt_num(u[2])} RUB\n"

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"users_page_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"users_page_{page+1}"))

    kb = []
    if nav_buttons:
        kb.append(nav_buttons)
    kb.append([InlineKeyboardButton(text="🔍 Посмотреть пользователя", callback_data="users_select")])
    kb.append([InlineKeyboardButton(text="🔙 Назад в админку", callback_data="admin_panel")])

    if img_exists("ПОЛЬЗОВАТЕЛИ.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("ПОЛЬЗОВАТЕЛИ.jpg")), caption=text, parse_mode="Markdown"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(lambda c: c.data.startswith("users_page_"))
async def users_page_cb(call: CallbackQuery):
    page = int(call.data.split("_")[2])
    await show_users_page(call, page)
    await call.answer()

@dp.callback_query(lambda c: c.data == "users_select")
async def users_select_cb(call: CallbackQuery):
    users = db.get_all_users(50, 0)
    if not users:
        await call.answer("Нет пользователей", show_alert=True)
        return

    kb = []
    for u in users[:20]:
        kb.append([InlineKeyboardButton(text=f"{u[0]} | @{u[1] or 'без имени'}", callback_data=f"user_info_{u[0]}")])
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")])

    text = "👥 *Выберите пользователя:*"
    if img_exists("ПОЛЬЗОВАТЕЛИ.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("ПОЛЬЗОВАТЕЛИ.jpg")), caption=text, parse_mode="Markdown"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()

@dp.callback_query(lambda c: c.data.startswith("user_info_"))
async def user_info_cb(call: CallbackQuery, state: FSMContext):
    uid = int(call.data.split("_")[2])
    await state.update_data(target_user_id=uid)
    user = db.get_user_dict(uid)
    
    if not user:
        await call.answer("Пользователь не найден", show_alert=True)
        return
    
    premium_info = db.get_premium_info(uid)
    is_premium_user = premium_info.get("active", False)
    
    # === РАСШИРЕННАЯ ДЕТАЛИЗАЦИЯ БАЛАНСА ===
    try:
        rates = await currency_api.fetch_rates('RUB')
    except Exception:
        rates = {'USD': 73, 'EUR': 83, 'TON': 120, 'USDT': 73, 'STARS': 2,
                 'UAH': 1.6, 'KZT': 0.15, 'UZS': 0.0061, 'BYN': 26}
    
    total_rub = 0
    balances_lines = []
    available_lines = []
    commission_rate = 0.0 if is_premium_user else COMMISSION_WITHDRAW
    
    for code, info in CURRENCIES.items():
        bal = db.get_balance(uid, code)
        if bal > 0:
            if code == 'RUB':
                rub_val = bal
            else:
                rate = rates.get(code, 0)
                rub_val = bal * rate if rate > 0 else 0
            total_rub += rub_val
            balances_lines.append(f"• {fmt_num(bal)} {info['symbol']} ({code}) ≈ {fmt_num(rub_val)} RUB")
            
            if commission_rate > 0:
                available = quantize_amount(bal * (1 - commission_rate))
                available_lines.append(f"  ➡️ {fmt_num(available)} {code} (с учётом комиссии)")
    
    balances_text = "\n".join(balances_lines) if balances_lines else "• 0 RUB\n"
    
    if is_premium_user:
        available_total_text = f"💰 *Доступно к выводу:* {fmt_num(total_rub)} RUB (комиссия 0% — Premium)"
    else:
        commission_amount = quantize_amount(total_rub * COMMISSION_WITHDRAW)
        available_total_rub = quantize_amount(total_rub * (1 - COMMISSION_WITHDRAW))
        available_total_text = (
            f"💰 *Доступно к выводу:* {fmt_num(available_total_rub)} RUB "
            f"(с учётом комиссии {int(COMMISSION_WITHDRAW*100)}%, удержано {fmt_num(commission_amount)} RUB)"
        )
        if available_lines:
            available_total_text += "\n" + "\n".join(available_lines)
    
    premium_status = "✅ Активен" if is_premium_user else "❌ Не активен"
    if premium_info.get("active") and premium_info.get("expires"):
        if premium_info["expires"]:
            try:
                exp_date = datetime.strptime(premium_info["expires"], '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y')
                premium_status += f" (до {exp_date})"
            except:
                pass
    
    # === ИСПРАВЛЕНИЕ: показываем username создателя ===
    granted_info = ""
    if premium_info.get("granted_by") and premium_info["granted_by"] != 0:
        creator_id = premium_info["granted_by"]
        creator = db.get_user_dict(creator_id)
        if creator and creator.get('username'):
            granted_info = f"\n👑 Выдал: @{creator.get('username')}"
        else:
            granted_info = f"\n👑 Выдал: `{creator_id}`"
        if premium_info.get("granted_at"):
            granted_info += f"\n📅 Дата выдачи: {premium_info['granted_at'][:16]}"
        if premium_info.get("duration_days"):
            granted_info += f"\n📆 Длительность: {premium_info['duration_days']} дней"
    
    reg_date = user.get('created_at', '?')
    text = (
        f"👤 *Информация о пользователе*\n\n"
        f"🆔 ID: `{user['user_id']}`\n"
        f"📝 Username: @{escape_md(user['username'] or 'без username')}\n"
        f"📅 Регистрация: {str(reg_date)[:16] if reg_date != '?' else '?'}\n\n"
        f"💰 *Балансы (детализация):*\n{balances_text}\n"
        f"💵 *Общий эквивалент:* {fmt_num(total_rub)} RUB\n"
        f"{available_total_text}\n\n"
        f"⭐ *Premium:* {premium_status}{granted_info}\n\n"
        f"💳 Карта: {escape_md(user.get('card_details') or 'не указана')}\n"
        f"📱 TON: {escape_md(user.get('ton') or user.get('ton_wallet') or 'не указан')}"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Зачислить", callback_data=f"admin_credit_user_{uid}"),
        InlineKeyboardButton(text="💸 Списать", callback_data=f"admin_debit_user_{uid}")],
        [InlineKeyboardButton(text="⭐ Выдать Premium", callback_data=f"admin_premium_user_{uid}"),
        InlineKeyboardButton(text="❌ Забрать Premium", callback_data=f"premium_remove_{uid}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="users_select")]
    ])
    
    if img_exists("ПОЛЬЗОВАТЕЛИ.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("ПОЛЬЗОВАТЕЛИ.jpg")), caption=text, parse_mode="Markdown"),
            reply_markup=kb
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    await call.answer()

# ========== АДМИН-ВВОД ==========
@dp.message(StateFilter(AdminCreditState.uid))
async def admin_credit_uid(msg: types.Message, state: FSMContext):
    try:
        user_id = int(msg.text)
        user = db.get_user(user_id)
        if not user:
            await msg.answer("❌ Пользователь не найден", reply_markup=admin_cancel_kb())
            return
        await state.update_data(user_id=user_id)
        
        await msg.answer(
            f"💰 *Зачисление средств для пользователя {user_id}*\n\n"
            f"Выберите валюту для зачисления:",
            parse_mode="Markdown",
            reply_markup=admin_currency_kb("credit_amount", user_id)
        )
        # Не очищаем state — пользователь должен выбрать валюту
    except ValueError:
        await msg.answer("❌ Введите ID числом", reply_markup=admin_cancel_kb())

@dp.message(StateFilter(AdminCreditState.amount))
async def admin_credit_amount(msg: types.Message, state: FSMContext):
    try:
        amount = int(msg.text)
        if amount <= 0:
            await msg.answer("❌ Сумма должна быть больше 0", reply_markup=admin_cancel_kb())
            return
        data = await state.get_data()
        uid = data.get('user_id', data.get('uid', data.get('target_user_id', 0)))
        currency = data.get('currency', 'RUB')
        db.update_balance(uid, currency, amount)
        db.credit_referral_deposit_commission(uid, currency, amount)
        await bot.send_message(uid, f"💰 Вам зачислено {fmt_num(amount)} {currency}!")
        await state.clear()
        await msg.answer(f"✅ Зачислено {fmt_num(amount)} {currency} пользователю `{uid}`", parse_mode="Markdown")
        await send_admin_menu(msg)
    except ValueError:
        await msg.answer("❌ Введите число", reply_markup=admin_cancel_kb())

@dp.message(StateFilter(AdminDebitState.uid))
async def admin_debit_uid(msg: types.Message, state: FSMContext):
    try:
        user_id = int(msg.text)
        user = db.get_user(user_id)
        if not user:
            await msg.answer("❌ Пользователь не найден", reply_markup=admin_cancel_kb())
            return
        await state.update_data(user_id=user_id)
        
        await msg.answer(
            f"💸 *Списание средств у пользователя {user_id}*\n\n"
            f"Балансы пользователя:\n"
            f"{await get_user_balances_text(user_id)}\n\n"
            f"Выберите валюту для списания:",
            parse_mode="Markdown",
            reply_markup=admin_currency_kb("debit_amount", user_id)
        )
        # Не очищаем state — пользователь должен выбрать валюту
    except ValueError:
        await msg.answer("❌ Введите ID числом", reply_markup=admin_cancel_kb())

@dp.message(StateFilter(AdminDebitState.amount))
async def admin_debit_amount(msg: types.Message, state: FSMContext):
    try:
        amount = int(msg.text)
        if amount <= 0:
            await msg.answer("❌ Сумма должна быть больше 0", reply_markup=admin_cancel_kb())
            return
        data = await state.get_data()
        uid = data.get('user_id', data.get('uid', data.get('target_user_id', 0)))
        currency = data.get('currency', 'RUB')
        current_balance = db.get_balance(uid, currency)
        if amount > current_balance:
            await msg.answer(f"❌ Недостаточно средств. Баланс: {fmt_num(current_balance)} {currency}", reply_markup=admin_cancel_kb())
            return
        db.update_balance(uid, currency, -amount)
        await msg.answer(f"✅ Списано {fmt_num(amount)} {currency} у {uid}")
        await state.clear()
        await bot.send_message(uid, f"💸 С вашего баланса списано {fmt_num(amount)} {currency}")
        await send_admin_menu(msg)
    except ValueError:
        await msg.answer("❌ Введите число", reply_markup=admin_cancel_kb())

@dp.message(StateFilter(AdminPremiumState.user_id))
async def admin_premium_user_id(msg: types.Message, state: FSMContext):
    try:
        user_id = int(msg.text)
        if not db.get_user(user_id):
            await msg.answer("❌ Пользователь не найден", reply_markup=admin_cancel_kb())
            return
        await state.update_data(user_id=user_id)
        
        await msg.answer(
            f"👑 *Premium подписка для пользователя {user_id}*\n\nВыберите длительность:",
            parse_mode="Markdown",
            reply_markup=premium_days_kb(user_id)
        )
    except ValueError:
        await msg.answer("❌ Введите ID числом", reply_markup=admin_cancel_kb())

@dp.callback_query(lambda c: c.data.startswith("premium_days_"))
async def premium_days_cb(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    parts = call.data.split("_")
    days_str = parts[2]
    user_id = int(parts[3])
    
    if days_str == "forever":
        days = 36500
        days_text = "FOREVER"
    else:
        days = int(days_str)
        days_text = f"{days} дней"
    
    db.set_premium(user_id, days, call.from_user.id)
    
    text_success = (
        f"✅ *Premium подписка выдана!*\n\n"
        f"👤 Пользователь: `{user_id}`\n"
        f"📅 Длительность: {days_text}\n"
        f"👑 Выдал: @{call.from_user.username or call.from_user.id}\n"
        f"📆 Дата выдачи: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    if img_exists("PREMIUM ПОДПИСКА.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("PREMIUM ПОДПИСКА.jpg")), caption=text_success, parse_mode="Markdown"),
            reply_markup=admin_kb()
        )
    else:
        await call.message.edit_text(text_success, parse_mode="Markdown", reply_markup=admin_kb())
    
    await bot.send_message(
        user_id,
        f"🎉 *Поздравляем!*\n\n"
        f"Вам выдана Premium подписка на *{days_text}*!\n\n"
        f"✨ *Преимущества:*\n"
        f"• Комиссия при получении средств: *0%*\n"
        f"• Комиссия при выводе: *0%*\n"
        f"• Приоритетная поддержка",
        parse_mode="Markdown"
    )
    
    await call.answer()

@dp.callback_query(lambda c: c.data.startswith("premium_remove_"))
async def premium_remove_cb(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    user_id = int(call.data.split("_")[2])
    db.remove_premium(user_id)
    
    text_success = (
        f"✅ *Premium подписка отозвана!*\n\n"
        f"👤 Пользователь: `{user_id}`"
    )
    if img_exists("PREMIUM ПОДПИСКА.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("PREMIUM ПОДПИСКА.jpg")), caption=text_success, parse_mode="Markdown"),
            reply_markup=admin_kb()
        )
    else:
        await call.message.edit_text(text_success, parse_mode="Markdown", reply_markup=admin_kb())
    
    await bot.send_message(
        user_id,
        f"⚠️ *Ваша Premium подписка была отозвана администратором.*",
        parse_mode="Markdown"
    )
    
    await call.answer()

@dp.callback_query(lambda c: c.data == "premium_back_user")
async def premium_back_user_cb(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("⛔ Доступ запрещен", show_alert=True)
        return
    data = await state.get_data()
    uid = data.get("target_user_id")
    if not uid:
        await call.answer("❌ Сессия истекла", show_alert=True)
        await admin_panel_cb(call)
        return
    call.data = f"user_info_{uid}"
    await user_info_cb(call, state)

@dp.callback_query(lambda c: c.data == "admin_promocodes")
async def admin_promocodes_cb(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("⛔ Доступ запрещен", show_alert=True)
    
    text = (
        "🎫 *Управление промокодами*\n\n"
        "Здесь вы можете создавать, просматривать и удалять промокоды."
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Сгенерировать промокод", callback_data="admin_add_promo")],
        [InlineKeyboardButton(text="📋 Список активных промокодов", callback_data="admin_active_promos")],
        [InlineKeyboardButton(text="❌ Удалить промокод", callback_data="admin_delete_promo_list")],
        [InlineKeyboardButton(text="📜 История всех промокодов", callback_data="admin_promo_history")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
    ])
    await edit_or_new(call, text, kb, "ПРОМОКОДЫ.jpg")
    await call.answer()


@dp.callback_query(lambda c: c.data == "admin_active_promos")
async def admin_active_promos_cb(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("⛔ Доступ запрещен", show_alert=True)
    
    db.cursor.execute("""
        SELECT code, amount, max_uses, used_count, expires_at, active 
        FROM promocodes 
        WHERE active = 1 AND (expires_at IS NULL OR expires_at > datetime('now'))
        ORDER BY code
    """)
    promos = db.cursor.fetchall()
    
    if not promos:
        text = "📭 *Нет действующих промокодов*"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать промокод", callback_data="admin_add_promo")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_promocodes")]
        ])
        await edit_or_new(call, text, kb, "ПРОМОКОДЫ.jpg")
        await call.answer()
        return
    
    text = "📋 *Действующие промокоды*\n\n"
    kb = []
    
    for promo in promos:
        code, amount, max_uses, used_count, expires_at, active = promo
        expires = expires_at if expires_at else "∞ (бессрочный)"
        if expires != "∞ (бессрочный)":
            try:
                exp_date = datetime.strptime(expires, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y')
                expires = exp_date
            except:
                pass
        
        # Получаем информацию о создателе
        db.cursor.execute("SELECT admin_id, timestamp FROM admin_logs WHERE action = 'create_promo' AND target_id = ? ORDER BY timestamp DESC LIMIT 1", (code,))
        admin_log = db.cursor.fetchone()
        creator_info = "неизвестен"
        if admin_log:
            creator_id = admin_log[0]
            user = db.get_user_dict(creator_id)
            if user:
                creator_info = f"@{user.get('username') or creator_id}" if user.get('username') else str(creator_id)
        
        text += f"• `{code}`\n"
        text += f"  💰 {amount} RUB | {used_count}/{max_uses} использований\n"
        text += f"  📅 До: {expires}\n"
        text += f"  👤 Создал: {creator_info}\n\n"
        
        kb.append([InlineKeyboardButton(
            text=f"📊 {code}",
            callback_data=f"admin_promo_stats_{code}"
        )])
    
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_promocodes")])
    
    await edit_or_new(call, text, InlineKeyboardMarkup(inline_keyboard=kb), "ПРОМОКОДЫ.jpg")
    await call.answer()


@dp.callback_query(lambda c: c.data.startswith("admin_promo_stats_"))
async def admin_promo_stats_cb(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("⛔ Доступ запрещен", show_alert=True)
    
    code = call.data.replace("admin_promo_stats_", "")
    promo = db.get_promocode(code)
    
    if not promo:
        await call.answer("❌ Промокод не найден", show_alert=True)
        return
    
    # Получаем информацию о создателе
    db.cursor.execute("SELECT admin_id, timestamp FROM admin_logs WHERE action = 'create_promo' AND target_id = ? ORDER BY timestamp DESC LIMIT 1", (code,))
    admin_log = db.cursor.fetchone()
    creator_info = "неизвестен"
    created_at = "неизвестно"
    if admin_log:
        creator_id = admin_log[0]
        created_at = admin_log[1]
        user = db.get_user_dict(creator_id)
        if user:
            creator_info = f"@{user.get('username') or creator_id}" if user.get('username') else str(creator_id)
    
    status = "✅ Активен" if promo['active'] else "❌ Неактивен"
    expires = promo['expires_at'] if promo['expires_at'] else "∞ (бессрочный)"
    if expires != "∞ (бессрочный)":
        try:
            exp_date = datetime.strptime(expires, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y %H:%M')
            expires = exp_date
        except:
            pass
    
    text = (
        f"📊 *Статистика промокода*\n\n"
        f"📝 Код: `{code}`\n"
        f"💰 Сумма: {promo['amount']} RUB\n"
        f"📋 Использований: {promo['used_count']}/{promo['max_uses']}\n"
        f"📅 Действует до: {expires}\n"
        f"📌 Статус: {status}\n"
        f"👤 Создал: {creator_info}\n"
        f"📆 Создан: {created_at if created_at != 'неизвестно' else 'неизвестно'}\n"
    )
    
    # Список использований
    db.cursor.execute("""
        SELECT user_id, used_at FROM promocode_uses 
        WHERE promo_code = ? 
        ORDER BY used_at DESC LIMIT 10
    """, (code,))
    uses = db.cursor.fetchall()
    
    if uses:
        text += "\n*Последние использований:*\n"
        for user_id, used_at in uses:
            user = db.get_user_dict(user_id)
            username = f"@{user.get('username')}" if user and user.get('username') else str(user_id)
            text += f"• {username} — {used_at[:16]}\n"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Деактивировать", callback_data=f"admin_toggle_promo_{code}")],
        [InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"admin_delconf_{code}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_active_promos")]
    ])
    
    await edit_or_new(call, text, kb, "ПРОМОКОДЫ.jpg")
    await call.answer()


@dp.callback_query(lambda c: c.data == "admin_used_promos")
async def admin_used_promos_cb(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("⛔ Доступ запрещен", show_alert=True)
    
    db.cursor.execute("""
        SELECT code, amount, max_uses, used_count, expires_at, active 
        FROM promocodes 
        WHERE active = 0 OR (expires_at IS NOT NULL AND expires_at <= datetime('now'))
        ORDER BY expires_at DESC
    """)
    promos = db.cursor.fetchall()
    
    if not promos:
        text = "📭 *Нет использованных/просроченных промокодов*"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_promocodes")]
        ])
        await edit_or_new(call, text, kb, "ПРОМОКОДЫ.jpg")
        await call.answer()
        return
    
    text = "📋 *Использованные/просроченные промокоды*\n\n"
    
    for promo in promos:
        code, amount, max_uses, used_count, expires_at, active = promo
        status = "❌ Истёк" if expires_at and expires_at <= datetime.now().strftime('%Y-%m-%d %H:%M:%S') else "❌ Деактивирован"
        expires = expires_at if expires_at else "∞"
        if expires != "∞":
            try:
                exp_date = datetime.strptime(expires, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y')
                expires = exp_date
            except:
                pass
        
        text += f"• `{code}` — {status}\n"
        text += f"  💰 {amount} RUB | {used_count}/{max_uses} использований\n"
        text += f"  📅 Истёк: {expires}\n\n"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_promocodes")]
    ])
    
    await edit_or_new(call, text, kb, "ПРОМОКОДЫ.jpg")
    await call.answer()

@dp.callback_query(lambda c: c.data == "admin_promo_history")
async def admin_promo_history_cb(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("⛔ Доступ запрещен", show_alert=True)
    
    # Все промокоды (активные, удалённые, истёкшие) без фильтра
    db.cursor.execute("""
        SELECT code FROM promocodes ORDER BY created_at DESC, code
    """)
    codes = db.cursor.fetchall()
    
    if not codes:
        text = "📭 *История промокодов пуста*"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_promocodes")]
        ])
        await edit_or_new(call, text, kb, "ПРОМОКОДЫ.jpg")
        await call.answer()
        return
    
    text = "📜 *История всех промокодов*\n\n"
    kb = []
    
    for (code,) in codes:
        promo = db.get_promocode(code)
        if promo.get('active'):
            status_emoji = "✅"
        elif promo.get('deleted_at'):
            status_emoji = "🗑️"
        else:
            status_emoji = "❌"
        text += f"{status_emoji} `{code}`\n"
        kb.append([InlineKeyboardButton(
            text=f"{status_emoji} {code}",
            callback_data=f"admin_promo_history_detail_{code}"
        )])
    
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_promocodes")])
    
    await edit_or_new(call, text, InlineKeyboardMarkup(inline_keyboard=kb), "ПРОМОКОДЫ.jpg")
    await call.answer()

@dp.callback_query(lambda c: c.data.startswith("admin_promo_history_detail_"))
async def admin_promo_history_detail_cb(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("⛔ Доступ запрещен", show_alert=True)
    
    code = call.data.replace("admin_promo_history_detail_", "")
    promo = db.get_promocode(code)
    
    if not promo:
        await call.answer("❌ Промокод не найден", show_alert=True)
        return
    
    # Информация о создателе
    created_by_id = promo.get('created_by', 0)
    created_at = promo.get('created_at', 'неизвестно')
    creator_info = f"`{created_by_id}`"
    if created_by_id:
        creator = db.get_user_dict(created_by_id)
        if creator and creator.get('username'):
            creator_info = f"@{creator.get('username')}"
    
    # Информация об удалении
    deleted_info = ""
    if promo.get('deleted_at'):
        deleted_by_id = promo.get('deleted_by', 0)
        deleter_info = f"`{deleted_by_id}`"
        if deleted_by_id:
            deleter = db.get_user_dict(deleted_by_id)
            if deleter and deleter.get('username'):
                deleter_info = f"@{deleter.get('username')}"
        delete_reason = promo.get('delete_reason', 'manual')
        reason_text = {
            'manual': 'Удалён вручную администратором',
            'expired': 'Истёк по времени',
            'limit_reached': 'Исчерпан лимит активаций'
        }.get(delete_reason, delete_reason)
        deleted_info = (
            f"\n🗑️ *Удалён:* {promo['deleted_at'][:19]}\n"
            f"👤 Кем удалён: {deleter_info}\n"
            f"📋 Причина: {reason_text}"
        )
    
    # Статус
    if promo.get('active'):
        status = "✅ Активен"
    elif promo.get('deleted_at'):
        status = "🗑️ Удалён"
    elif promo.get('expires_at') and promo['expires_at'] <= datetime.now().strftime('%Y-%m-%d %H:%M:%S'):
        status = "❌ Истёк по времени"
    elif promo.get('used_count', 0) >= promo.get('max_uses', 1):
        status = "❌ Исчерпан лимит активаций"
    else:
        status = "❌ Неактивен"
    
    # Срок действия
    expires = promo.get('expires_at')
    expires_text = "♾️ Бессрочный" if not expires else expires[:19]
    
    # Количество активировавших пользователей
    db.cursor.execute("SELECT COUNT(*) FROM promocode_uses WHERE promo_code = ?", (code,))
    total_users_activated = db.cursor.fetchone()[0]
    
    text = (
        f"📜 *Детали промокода*\n\n"
        f"📝 Код: `{code}`\n"
        f"💰 Сумма: {promo['amount']} RUB\n"
        f"📋 Использований: {promo['used_count']}/{promo['max_uses']}\n"
        f"👥 Активировало пользователей: {total_users_activated}\n"
        f"📌 Статус: {status}\n"
        f"📅 Создан: {created_at[:19] if created_at != 'неизвестно' else 'неизвестно'}\n"
        f"👤 Автор: {creator_info}\n"
        f"📅 Действует до: {expires_text}"
        f"{deleted_info}"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_promo_history")]
    ])
    
    await edit_or_new(call, text, kb, "ПРОМОКОДЫ.jpg")
    await call.answer()

@dp.callback_query(lambda c: c.data == "admin_add_promo")
async def admin_add_promo_cb(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("⛔ Доступ запрещен", show_alert=True)
    
    await state.set_state(AdminStates.promo_code)
    
    text = (
        "📝 *Создание промокода (Шаг 1 из 6)*\n\n"
        "Введите код промокода:\n"
        "• Только буквы и цифры\n"
        "• Без пробелов\n"
        "• Уникальное название"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_promocodes")]
    ])
    
    await edit_or_new(call, text, kb)
    await call.answer()

@dp.message(AdminStates.promo_code)
async def admin_promo_code_msg(msg: types.Message, state: FSMContext):
    code = msg.text.strip().upper()
    
    # Проверяем, что только буквы и цифры
    if not code.isalnum():
        await msg.answer("❌ Код должен содержать только буквы и цифры. Попробуйте снова:", reply_markup=cancel_kb())
        return
    
    # Проверяем, что код уникален
    if db.get_promocode(code):
        await msg.answer("❌ Такой промокод уже существует. Введите другой код:", reply_markup=cancel_kb())
        return
    
    await state.update_data(promo_code=code)
    await state.set_state(AdminStates.promo_amount)
    
    text = (
        f"📝 *Создание промокода (Шаг 2 из 6)*\n\n"
        f"Код: `{code}`\n\n"
        f"💰 Введите сумму бонуса в RUB:\n"
        f"• Только число\n"
        f"• Например: 50, 100, 500"
    )
    await msg.answer(text, parse_mode="Markdown", reply_markup=cancel_kb())

@dp.message(AdminStates.promo_amount)
async def admin_promo_amount_msg(msg: types.Message, state: FSMContext):
    try:
        amount = float(msg.text.strip())
        if amount <= 0:
            await msg.answer("❌ Сумма должна быть больше 0. Введите число:", reply_markup=cancel_kb())
            return
        if amount > 100000:
            await msg.answer("❌ Сумма не может превышать 100 000 RUB. Введите меньше:", reply_markup=cancel_kb())
            return
    except ValueError:
        await msg.answer("❌ Введите число (например: 50, 100, 500):", reply_markup=cancel_kb())
        return
    
    await state.update_data(promo_amount=amount)
    await state.set_state(AdminStates.promo_type)
    
    data = await state.get_data()
    text = (
        f"📝 *Создание промокода (Шаг 3 из 6)*\n\n"
        f"Код: `{data['promo_code']}`\n"
        f"💰 Сумма: {amount} RUB\n\n"
        f"🔢 Выберите тип использований:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="♾️ Бесконечный (∞)", callback_data="promo_type_unlimited")],
        [InlineKeyboardButton(text="🔢 Ограниченный", callback_data="promo_type_limited")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_promo")]
    ])
    await msg.answer(text, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(lambda c: c.data.startswith("promo_type_"))
async def admin_promo_type_cb(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("⛔ Доступ запрещен", show_alert=True)
    
    promo_type = call.data.replace("promo_type_", "")
    await state.update_data(promo_type=promo_type)
    
    data = await state.get_data()
    
    if promo_type == "unlimited":
        await state.set_state(AdminStates.promo_expires_type)
        text = (
            f"📝 *Создание промокода (Шаг 4 из 6)*\n\n"
            f"Код: `{data['promo_code']}`\n"
            f"💰 Сумма: {data['promo_amount']} RUB\n"
            f"🔢 Использований: ♾️ Бесконечный\n\n"
            f"📅 Выберите срок действия:"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="♾️ Бессрочный", callback_data="promo_expires_forever")],
            [InlineKeyboardButton(text="📅 Конкретная дата", callback_data="promo_expires_date")],
            [InlineKeyboardButton(text="📆 Количество дней", callback_data="promo_expires_days")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_promo")]
        ])
        if img_exists("ПРОМОКОДЫ.jpg"):
            await call.message.edit_media(
                InputMediaPhoto(media=FSInputFile(img_path("ПРОМОКОДЫ.jpg")), caption=text, parse_mode="Markdown"),
                reply_markup=kb
            )
        else:
            await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
        await call.answer()
    else:
        await state.set_state(AdminStates.promo_max_uses)
        text = (
            f"📝 *Создание промокода (Шаг 4 из 6)*\n\n"
            f"Код: `{data['promo_code']}`\n"
            f"💰 Сумма: {data['promo_amount']} RUB\n\n"
            f"🔢 Введите максимальное количество использований:\n"
            f"• Только число\n"
            f"• Например: 10, 100, 1000"
        )
        if img_exists("ПРОМОКОДЫ.jpg"):
            await call.message.edit_media(
                InputMediaPhoto(media=FSInputFile(img_path("ПРОМОКОДЫ.jpg")), caption=text, parse_mode="Markdown"),
                reply_markup=cancel_kb()
            )
        else:
            await call.message.edit_text(text, parse_mode="Markdown", reply_markup=cancel_kb())
        await call.answer()

@dp.message(AdminStates.promo_max_uses)
async def admin_promo_max_uses_msg(msg: types.Message, state: FSMContext):
    try:
        max_uses = int(msg.text.strip())
        if max_uses <= 0:
            await msg.answer("❌ Количество использований должно быть больше 0. Введите число:", reply_markup=cancel_kb())
            return
        if max_uses > 1000000:
            await msg.answer("❌ Слишком большое число. Максимум 1 000 000:", reply_markup=cancel_kb())
            return
    except ValueError:
        await msg.answer("❌ Введите число (например: 10, 100, 1000):", reply_markup=cancel_kb())
        return
    
    await state.update_data(promo_max_uses=max_uses)
    await state.set_state(AdminStates.promo_expires_type)
    
    data = await state.get_data()
    text = (
        f"📝 *Создание промокода (Шаг 5 из 6)*\n\n"
        f"Код: `{data['promo_code']}`\n"
        f"💰 Сумма: {data['promo_amount']} RUB\n"
        f"🔢 Использований: {max_uses} раз(а)\n\n"
        f"📅 Выберите срок действия:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="♾️ Бессрочный", callback_data="promo_expires_forever")],
        [InlineKeyboardButton(text="📅 Конкретная дата", callback_data="promo_expires_date")],
        [InlineKeyboardButton(text="📆 Количество дней", callback_data="promo_expires_days")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_add_promo")]
    ])
    await msg.answer(text, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(lambda c: c.data.startswith("promo_expires_"))
async def admin_promo_expires_cb(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("⛔ Доступ запрещен", show_alert=True)
    
    expires_type = call.data.replace("promo_expires_", "")
    await state.update_data(promo_expires_type=expires_type)
    
    data = await state.get_data()
    
    if expires_type == "forever":
        await create_promo_final(call, state)
    elif expires_type == "date":
        await state.set_state(AdminStates.promo_expires_date)
        text = (
            f"📝 *Создание промокода (Шаг 6 из 6)*\n\n"
            f"Код: `{data['promo_code']}`\n"
            f"💰 Сумма: {data['promo_amount']} RUB\n"
            f"🔢 Использований: {data.get('promo_max_uses', '♾️ Бесконечный')}\n\n"
            f"📅 Введите дату окончания в формате:\n"
            f"`ДД.ММ.ГГГГ`\n\n"
            f"Например: `31.12.2026`"
        )
        if img_exists("ПРОМОКОДЫ.jpg"):
            await call.message.edit_media(
                InputMediaPhoto(media=FSInputFile(img_path("ПРОМОКОДЫ.jpg")), caption=text, parse_mode="Markdown"),
                reply_markup=cancel_kb()
            )
        else:
            await call.message.edit_text(text, parse_mode="Markdown", reply_markup=cancel_kb())
        await call.answer()
    elif expires_type == "days":
        await state.set_state(AdminStates.promo_expires_days)
        text = (
            f"📝 *Создание промокода (Шаг 6 из 6)*\n\n"
            f"Код: `{data['promo_code']}`\n"
            f"💰 Сумма: {data['promo_amount']} RUB\n"
            f"🔢 Использований: {data.get('promo_max_uses', '♾️ Бесконечный')}\n\n"
            f"📆 Введите количество дней действия:\n"
            f"• Только число\n"
            f"• Например: 30, 60, 365"
        )
        if img_exists("ПРОМОКОДЫ.jpg"):
            await call.message.edit_media(
                InputMediaPhoto(media=FSInputFile(img_path("ПРОМОКОДЫ.jpg")), caption=text, parse_mode="Markdown"),
                reply_markup=cancel_kb()
            )
        else:
            await call.message.edit_text(text, parse_mode="Markdown", reply_markup=cancel_kb())
        await call.answer()

@dp.message(AdminStates.promo_expires_date)
async def admin_promo_expires_date_msg(msg: types.Message, state: FSMContext):
    date_str = msg.text.strip()
    
    # Проверяем формат ДД.ММ.ГГГГ
    try:
        exp_date = datetime.strptime(date_str, '%d.%m.%Y')
        if exp_date < datetime.now():
            await msg.answer("❌ Дата не может быть в прошлом. Введите будущую дату:", reply_markup=cancel_kb())
            return
        await state.update_data(promo_expires_date=exp_date.strftime('%Y-%m-%d %H:%M:%S'))
    except ValueError:
        await msg.answer("❌ Неверный формат. Используйте ДД.ММ.ГГГГ (например: 31.12.2026):", reply_markup=cancel_kb())
        return
    
    await create_promo_final(msg, state)

@dp.message(AdminStates.promo_expires_days)
async def admin_promo_expires_days_msg(msg: types.Message, state: FSMContext):
    try:
        days = int(msg.text.strip())
        if days <= 0:
            await msg.answer("❌ Количество дней должно быть больше 0. Введите число:", reply_markup=cancel_kb())
            return
        if days > 3650:
            await msg.answer("❌ Максимум 3650 дней (10 лет). Введите меньше:", reply_markup=cancel_kb())
            return
    except ValueError:
        await msg.answer("❌ Введите число (например: 30, 60, 365):", reply_markup=cancel_kb())
        return
    
    await state.update_data(promo_expires_days=days)
    await create_promo_final(msg, state)

async def create_promo_final(event, state: FSMContext):
    """Финальное создание промокода"""
    data = await state.get_data()
    
    code = data.get('promo_code')
    amount = data.get('promo_amount')
    promo_type = data.get('promo_type', 'unlimited')
    expires_type = data.get('promo_expires_type')
    expires_date = data.get('promo_expires_date')
    expires_days = data.get('promo_expires_days')
    
    # Определяем max_uses
    if promo_type == 'unlimited':
        max_uses = 999999999  # Практически бесконечный
    else:
        max_uses = data.get('promo_max_uses', 1)
    
    # Определяем expires_at
    if expires_type == 'forever':
        expires_at = None
    elif expires_type == 'date' and expires_date:
        expires_at = expires_date
    elif expires_type == 'days' and expires_days:
        expires_at = (datetime.now() + timedelta(days=expires_days)).strftime('%Y-%m-%d %H:%M:%S')
    else:
        expires_at = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
    
    # Создаём промокод
    try:
        creator_id = event.from_user.id if hasattr(event, 'from_user') else event.chat.id
    except:
        creator_id = event.from_user.id
    db.create_promocode_with_expires(code, amount, max_uses, expires_at, created_by=creator_id)
    
    # Логируем создание
    try:
        user_id = event.from_user.id if hasattr(event, 'from_user') else event.chat.id
    except:
        user_id = event.from_user.id
    
    db.cursor.execute("""
        INSERT INTO admin_logs (admin_id, action, target_id, amount, timestamp)
        VALUES (?, 'create_promo', ?, ?, CURRENT_TIMESTAMP)
    """, (user_id, code, amount))
    db.conn.commit()
    
    # Формируем текст результата
    expires_text = "♾️ Бессрочный"
    if expires_type == 'date' and expires_date:
        try:
            expires_text = datetime.strptime(expires_date, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y')
        except:
            expires_text = expires_date
    elif expires_type == 'days' and expires_days:
        expires_text = f"{expires_days} дней (до {datetime.now().strftime('%d.%m.%Y')})"
    
    uses_text = "♾️ Бесконечный" if promo_type == 'unlimited' else f"{max_uses} раз(а)"
    
    text = (
        f"✅ *Промокод успешно создан!*\n\n"
        f"📝 Код: `{code}`\n"
        f"💰 Сумма: {amount} RUB\n"
        f"🔢 Использований: {uses_text}\n"
        f"📅 Действует: {expires_text}\n"
        f"👤 Создал: @{event.from_user.username or event.from_user.id}\n"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать ещё", callback_data="admin_add_promo")],
        [InlineKeyboardButton(text="📋 Действующие промокоды", callback_data="admin_active_promos")],
        [InlineKeyboardButton(text="🔙 Назад в админку", callback_data="admin_panel")]
    ])
    
    # Отправляем финальное сообщение
    if hasattr(event, 'message'):
        if img_exists("ПРОМОКОДЫ.jpg"):
            await event.message.edit_media(
                InputMediaPhoto(media=FSInputFile(img_path("ПРОМОКОДЫ.jpg")), caption=text, parse_mode="Markdown"),
                reply_markup=kb
            )
        else:
            await event.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await event.delete()
        await event.answer(text, parse_mode="Markdown", reply_markup=kb)
    
    await state.clear()

@dp.message(AdminStates.promo_expires_days)
async def admin_promo_expires_msg(msg: types.Message, state: FSMContext):
    text = msg.text.strip()
    expires_days = 30
    if text:
        try:
            expires_days = int(text)
            if expires_days <= 0: expires_days = 30
        except ValueError:
            pass
    
    data = await state.get_data()
    db.create_promocode(data['promo_code'], data['promo_amount'], data['promo_max_uses'], expires_days)
    
    # Логируем создание промокода
    db.cursor.execute("""
        INSERT INTO admin_logs (admin_id, action, target_id, amount, timestamp)
        VALUES (?, 'create_promo', ?, ?, CURRENT_TIMESTAMP)
    """, (msg.from_user.id, data['promo_code'], data['promo_amount']))
    db.conn.commit()
    
    await state.clear()
    await msg.answer(
        f"✅ *Промокод создан!*\n\n"
        f"📝 Код: `{data['promo_code']}`\n"
        f"💰 Сумма: {data['promo_amount']} RUB\n"
        f"📋 Использований: {data['promo_max_uses']}\n"
        f"⏰ Дней: {expires_days}\n"
        f"👤 Создал: @{msg.from_user.username or msg.from_user.id}",
        parse_mode="Markdown",
        reply_markup=back_kb()
    )

@dp.callback_query(lambda c: c.data.startswith("admin_toggle_promo_"))
async def admin_toggle_promo_cb(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("⛔ Доступ запрещен", show_alert=True)
    
    code = call.data.replace("admin_toggle_promo_", "")
    db.toggle_promocode(code)
    
    # Логируем
    db.cursor.execute("""
        INSERT INTO admin_logs (admin_id, action, target_id, timestamp)
        VALUES (?, 'toggle_promo', ?, CURRENT_TIMESTAMP)
    """, (call.from_user.id, code))
    db.conn.commit()
    
    await call.answer("🔄 Статус промокода изменён!", show_alert=True)
    await admin_active_promos_cb(call)

@dp.callback_query(lambda c: c.data == "admin_delete_promo_list")
async def admin_delete_promo_list_cb(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("⛔ Доступ запрещен", show_alert=True)
    
    db.cursor.execute("""
        SELECT code, amount, max_uses, used_count, expires_at, active 
        FROM promocodes 
        WHERE active = 1 AND (expires_at IS NULL OR expires_at > datetime('now'))
        ORDER BY code
    """)
    promos = db.cursor.fetchall()
    
    if not promos:
        text = "📭 *Нет действующих промокодов для удаления*"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать промокод", callback_data="admin_add_promo")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_promocodes")]
        ])
        await edit_or_new(call, text, kb, "ПРОМОКОДЫ.jpg")
        await call.answer()
        return
    
    text = "❌ *Выберите промокод для удаления*\n\n"
    kb = []
    
    for promo in promos:
        code, amount, max_uses, used_count, expires_at, active = promo
        text += f"• `{code}` — {amount} RUB ({used_count}/{max_uses})\n"
        kb.append([InlineKeyboardButton(
            text=f"🗑️ {code}",
            callback_data=f"admin_delconf_{code}"
        )])
    
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_promocodes")])
    
    await edit_or_new(call, text, InlineKeyboardMarkup(inline_keyboard=kb), "ПРОМОКОДЫ.jpg")
    await call.answer()


@dp.callback_query(lambda c: c.data.startswith("admin_delconf_"))
async def admin_delete_promo_confirm_cb(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("⛔ Доступ запрещен", show_alert=True)
    
    code = call.data.replace("admin_delconf_", "")
    
    # Подтверждение удаления
    text = (
        f"⚠️ *Подтверждение удаления*\n\n"
        f"Вы уверены, что хотите удалить промокод `{code}`?\n\n"
        f"Это действие нельзя отменить."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"admin_del_exec_{code}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_delete_promo_list")]
    ])
    
    await edit_or_new(call, text, kb, "ПРОМОКОДЫ.jpg")
    await call.answer()


@dp.callback_query(lambda c: c.data.startswith("admin_del_exec_"))
async def admin_delete_promo_exec_cb(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("⛔ Доступ запрещен", show_alert=True)
    
    code = call.data.replace("admin_del_exec_", "")
    
    db.delete_promocode(code, deleted_by=call.from_user.id, reason="manual")
    await call.answer(f"✅ Промокод {code} удалён!", show_alert=True)
    await admin_delete_promo_list_cb(call)

@dp.callback_query(lambda c: c.data.startswith("admin_credit_user_"))
async def admin_credit_user_from_info(call: CallbackQuery, state: FSMContext):
    uid = int(call.data.split("_")[3])
    await state.update_data(uid=uid, target_user_id=uid)
    text = f"💰 *Зачисление средств пользователю {uid}*\n\nВыберите валюту для зачисления:"
    if img_exists("ПОЛЬЗОВАТЕЛИ.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("ПОЛЬЗОВАТЕЛИ.jpg")), caption=text, parse_mode="Markdown"),
            reply_markup=admin_currency_kb("credit_amount", uid, back_to_user=True)
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=admin_currency_kb("credit_amount", uid, back_to_user=True))
    await call.answer()

@dp.callback_query(lambda c: c.data.startswith("admin_debit_user_"))
async def admin_debit_user_from_info(call: CallbackQuery, state: FSMContext):
    uid = int(call.data.split("_")[3])
    await state.update_data(uid=uid, target_user_id=uid)
    text = (
        f"💸 *Списание средств у пользователя {uid}*\n\n"
        f"Балансы пользователя:\n"
        f"{await get_user_balances_text(uid)}\n\n"
        f"Выберите валюту для списания:"
    )
    if img_exists("ПОЛЬЗОВАТЕЛИ.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("ПОЛЬЗОВАТЕЛИ.jpg")), caption=text, parse_mode="Markdown"),
            reply_markup=admin_currency_kb("debit_amount", uid, back_to_user=True)
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=admin_currency_kb("debit_amount", uid, back_to_user=True))
    await call.answer()

@dp.callback_query(lambda c: c.data.startswith("admin_premium_user_"))
async def admin_premium_user_from_info(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    user_id = int(call.data.split("_")[3])
    await state.update_data(target_user_id=user_id)
    text = f"👑 *Premium подписка для пользователя {user_id}*\n\nВыберите длительность:"
    if img_exists("PREMIUM ПОДПИСКА.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("PREMIUM ПОДПИСКА.jpg")), caption=text, parse_mode="Markdown"),
            reply_markup=premium_days_kb(user_id, back_to_user=True)
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=premium_days_kb(user_id, back_to_user=True))
    await call.answer()

@dp.callback_query(lambda c: c.data.startswith("credit_amount_"))
async def credit_amount_currency_cb(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    parts = call.data.split("_")
    currency = parts[2]
    user_id = int(parts[3])
    
    await state.update_data(user_id=user_id, currency=currency)
    await state.set_state(AdminCreditState.amount)
    
    # Определяем, откуда пришли: если есть target_user_id — возвращаемся в карточку
    data = await state.get_data()
    back_uid = data.get('target_user_id', user_id)
    back_kb = admin_back_kb(back_uid)
    
    text = (
        f"💰 *Зачисление средств*\n\n"
        f"👤 Пользователь: `{user_id}`\n"
        f"💱 Валюта: {currency}\n\n"
        f"💵 *Введите сумму для зачисления:*"
    )
    if img_exists("ПОЛЬЗОВАТЕЛИ.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("ПОЛЬЗОВАТЕЛИ.jpg")), caption=text, parse_mode="Markdown"),
            reply_markup=back_kb
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=back_kb)
    await call.answer()

@dp.callback_query(lambda c: c.data.startswith("admin_back_user_"))
async def admin_back_to_user_cb(call: CallbackQuery, state: FSMContext):
    """Возвращает админа в карточку пользователя из подменю зачисления/списания/premium"""
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("⛔ Доступ запрещен", show_alert=True)
        return
    data = await state.get_data()
    uid = data.get('target_user_id') or data.get('uid') or data.get('user_id')
    if not uid:
        uid = int(call.data.replace("admin_back_user_", ""))
    await state.update_data(target_user_id=uid)
    await user_info_cb(call, state)

@dp.callback_query(lambda c: c.data.startswith("debit_amount_"))
async def debit_amount_currency_cb(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    parts = call.data.split("_")
    currency = parts[2]
    user_id = int(parts[3])
    
    await state.update_data(user_id=user_id, currency=currency)
    await state.set_state(AdminDebitState.amount)
    
    # Определяем, откуда пришли
    data = await state.get_data()
    back_uid = data.get('target_user_id', user_id)
    back_kb = admin_back_kb(back_uid)
    
    text = (
        f"💸 *Списание средств*\n\n"
        f"👤 Пользователь: `{user_id}`\n"
        f"💱 Валюта: {currency}\n"
        f"💰 Текущий баланс: {db.get_balance(user_id, currency)} {currency}\n\n"
        f"💵 *Введите сумму для списания:*"
    )
    if img_exists("ПОЛЬЗОВАТЕЛИ.jpg"):
        await call.message.edit_media(
            InputMediaPhoto(media=FSInputFile(img_path("ПОЛЬЗОВАТЕЛИ.jpg")), caption=text, parse_mode="Markdown"),
            reply_markup=back_kb
        )
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=back_kb)
    await call.answer()

@dp.message(StateFilter(AdminMailingState.title))
async def mailing_title(msg: types.Message, state: FSMContext):
    await state.update_data(title=msg.text)
    await state.set_state(AdminMailingState.text)
    await msg.answer("📝 *Введите текст рассылки:*", parse_mode="Markdown", reply_markup=cancel_kb())

@dp.message(StateFilter(AdminMailingState.text))
async def mailing_text(msg: types.Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        await msg.answer("⛔ Доступ запрещен")
        await state.clear()
        return
    
    data = await state.get_data()
    users = db.get_all_users_for_mailing()
    sent = 0
    
    await msg.answer("🚀 Рассылка начата...")
    
    for uid in users:
        try:
            await bot.send_message(
                uid,
                f"📢 *{escape_md(data['title'])}*\n\n{escape_md(msg.text)}",
                parse_mode="Markdown"
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"Не удалось отправить {uid}: {e}")
    
    await state.clear()
    await msg.answer(
        f"✅ Рассылка завершена!\nОтправлено: {sent} пользователям",
        parse_mode="Markdown",
        reply_markup=admin_kb()
    )

@dp.callback_query(lambda c: c.data.startswith("dispute_decide_"))
async def dispute_decide_cb(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("⛔ Доступ запрещен", show_alert=True)
        return

    parts = call.data.split("_")
    decision = parts[2]
    dispute_id = int(parts[3])

    async with deal_lock:
        result = db.resolve_dispute_with_decision(dispute_id, decision)

    if not result:
        await call.answer("❌ Спор уже обработан или данные некорректны", show_alert=True)
        return

    if result['decision'] == 'seller':
        await notify_user(result['seller_id'], f"✅ *Спор по сделке #{result['deal_id']} решён в вашу пользу.*\n\nНачислено {fmt_num(result['seller_amount'])} {result['currency']}")
        if result.get('buyer_id'):
            await notify_user(result['buyer_id'], f"⚖️ *Спор по сделке #{result['deal_id']} закрыт.*\n\nРешение: выплата продавцу.")
    else:
        if result.get('buyer_id'):
            await notify_user(result['buyer_id'], f"↩️ *Спор по сделке #{result['deal_id']} решён в вашу пользу.*\n\nВозврат: {fmt_num(result['amount'])} {result['currency']}")
        await notify_user(result['seller_id'], f"⚖️ *Спор по сделке #{result['deal_id']} закрыт.*\n\nРешение: возврат покупателю.")

    await call.message.edit_text(
        f"✅ *Спор #{dispute_id} обработан.*\n\nСделка #{result['deal_id']}\nРешение: {'выплата продавцу' if decision == 'seller' else 'возврат покупателю'}",
        parse_mode="Markdown"
    )
    await call.answer("✅ Решение применено", show_alert=True)

def build_deal_payload(deal: dict, viewer_id: int) -> dict:
    payment_instructions = None
    if deal.get('payment_method') == 'ton' and deal.get('status') in ('payment_pending', 'paid'):
        payment_instructions = {
            'address': deal.get('payment_address') or TON_ESCROW_ADDRESS,
            'comment': deal.get('payment_comment'),
            'amount': deal.get('payment_amount') or deal.get('amount')
        }

    return {
        'id': deal['id'],
        'item': deal['item'],
        'amount': deal['amount'],
        'currency': deal.get('currency', 'RUB'),
        'status': deal.get('status', 'awaiting'),
        'role': 'seller' if deal['seller'] == viewer_id else 'buyer',
        'created_at': deal.get('created'),
        'payment_method': deal.get('payment_method', 'internal'),
        'payment_instructions': payment_instructions,
        'can_open_dispute': deal.get('status') not in ('completed', 'cancelled')
    }


async def handle_api(request):
    # Проверяем авторизацию
    auth_user = await get_authenticated_webapp_user(request)
    if not auth_user:
        return JSONResponse(content={'error': 'Unauthorized Mini App request'}, status_code=401)
    
    # Получаем user_id из query параметров
    user_id_str = request.query_params.get('user_id', '0')
    try:
        user_id = int(user_id_str)
    except ValueError:
        return JSONResponse(content={'error': 'Invalid user_id format'}, status_code=400)
    
    # Проверяем, что пользователь совпадает
    if auth_user.get('id') != user_id:
        return JSONResponse(content={'error': 'User mismatch'}, status_code=403)
    
    # Получаем данные пользователя
    user = db.get_user_dict(user_id)
    if not user:
        return JSONResponse(content={'error': 'User not found'}, status_code=404)
    
    # Собираем балансы
    balances = {}
    for curr in ['RUB', 'BYN', 'UAH', 'KZT', 'UZS', 'EUR', 'USD', 'TON', 'USDT', 'STARS']:
        balances[curr] = db.get_balance(user_id, curr)
    
    # Получаем сделки
    deals = db.get_user_deals(user_id)
    deals_list = []
    for d in deals[:20]:
        deal = db.get_deal(d[0])
        if deal:
            deals_list.append(build_deal_payload(deal, user_id))
    
    # Получаем курсы валют
    try:
        rates = await currency_api.fetch_rates('RUB')
    except Exception:
        rates = currency_api.get_stale_cache('RUB')
    
    # Получаем реферальную информацию
    referral_code = user.get('referral_code') or str(user_id)
    referral_count = db.get_referral_count(user_id)
    referral_earnings = db.get_referral_earnings(user_id)
    
    return JSONResponse(content={
        'id': user['user_id'],
        'username': user['username'] or 'User',
        'firstName': user.get('first_name') or user['username'] or 'User',
        'balances': balances,
        'rates': rates,
        'rates_source': getattr(currency_api, 'last_source', 'unknown'),
        'is_premium': db.is_premium(user_id),
        'rating': user.get('rating', 0) or 0,
        'card': user.get('card_details') or '',
        'ton': user.get('ton') or user.get('ton_wallet') or '',
        'deals': deals_list,
        'referral_code': referral_code,
        'referral_count': referral_count,
        'referral_earnings': referral_earnings,
        'is_admin': user_id in ADMIN_IDS,
        'premium_until': db.get_premium_info(user_id).get('expires') if db.is_premium(user_id) else None,
        'ton_escrow_enabled': bool(TON_ESCROW_ADDRESS and TON_API_KEY)
    })

async def handle_create_deal(request):
    try:
        # Проверяем авторизацию
        auth_user = await get_authenticated_webapp_user(request)
        if not auth_user:
            return JSONResponse(content={'success': False, 'error': 'Unauthorized Mini App request'}, status_code=401)
        
        # Получаем данные
        data = await request.json()
        
        # Извлекаем user_id (может быть строкой или числом)
        user_id_raw = data.get('user_id')
        try:
            user_id = int(user_id_raw)
        except (ValueError, TypeError):
            return JSONResponse(content={'success': False, 'error': 'Invalid user_id'}, status_code=400)
        
        # Проверяем, что пользователь совпадает
        if auth_user.get('id') != user_id:
            return JSONResponse(content={'success': False, 'error': 'User mismatch'}, status_code=403)
        
        # Получаем данные сделки
        item_name = (data.get('item_name') or '').strip()
        try:
            price = float(data.get('price') or 0)
        except ValueError:
            return JSONResponse(content={'success': False, 'error': 'Invalid price'}, status_code=400)
        
        currency = data.get('currency', 'RUB')
        
        # Валидация
        allowed = ['RUB', 'BYN', 'UAH', 'KZT', 'UZS', 'EUR', 'USD', 'TON', 'USDT', 'STARS']
        if currency not in allowed:
            currency = 'RUB'
        
        if not item_name or len(item_name) > 200:
            return JSONResponse(content={'success': False, 'error': 'Invalid item name'}, status_code=400)
        
        if price <= 0:
            return JSONResponse(content={'success': False, 'error': 'Invalid price'}, status_code=400)
        
        # Проверяем пользователя
        user = db.get_user(user_id)
        if not user:
            return JSONResponse(content={'success': False, 'error': 'User not found'}, status_code=404)
        
        # Генерируем ID сделки
        import random
        deal_id = random.randint(100000, 999999)
        while db.get_deal(deal_id):
            deal_id = random.randint(100000, 999999)
        
        # Создаём сделку
        commission = db.get_user_commission(user_id, 'deal')
        db.create_deal(user_id, item_name, price, commission, deal_id, currency)
        
        # Генерируем код сделки
        deal_code = secrets.token_hex(3).upper()
        db.cursor.execute("UPDATE deals SET payment_comment = ? WHERE id = ?", (deal_code, deal_id))
        db.conn.commit()
        
        bot_link = f"tg://resolve?domain={BOT_USERNAME}&start=deal_{deal_id}"
        
        return JSONResponse(content={
            'success': True,
            'deal_id': deal_id,
            'deal_code': deal_code,
            'bot_link': bot_link,
            'item': item_name,
            'amount': price,
            'currency': currency,
            'commission': commission,
            'payment_method': 'internal'
        })
    except Exception as e:
        logger.exception('create_deal error')
        return JSONResponse(content={'success': False, 'error': str(e)}, status_code=500)

async def handle_save_card(request):
    try:
        auth_user = await get_authenticated_webapp_user(request)
        if not auth_user:
            return JSONResponse(content={'success': False, 'error': 'Unauthorized'}, status_code=401)
        
        data = await request.json()
        
        user_id_raw = data.get('user_id')
        try:
            user_id = int(user_id_raw)
        except (ValueError, TypeError):
            return JSONResponse(content={'success': False, 'error': 'Invalid user_id'}, status_code=400)
        
        if auth_user.get('id') != user_id:
            return JSONResponse(content={'success': False, 'error': 'User mismatch'}, status_code=403)
        
        card = (data.get('card') or '').replace(' ', '')
        if not card or not card.isdigit() or len(card) not in (16, 19):
            return JSONResponse(content={'success': False, 'error': 'Invalid card format'}, status_code=400)
        
        db.set_card(user_id, card)
        return JSONResponse(content={'success': True, 'message': 'Card saved'})
    except Exception as e:
        return JSONResponse(content={'success': False, 'error': str(e)}, status_code=500)

async def handle_save_ton(request):
    try:
        auth_user = await get_authenticated_webapp_user(request)
        if not auth_user:
            return JSONResponse(content={'success': False, 'error': 'Unauthorized'}, status_code=401)
        
        data = await request.json()
        
        user_id_raw = data.get('user_id')
        try:
            user_id = int(user_id_raw)
        except (ValueError, TypeError):
            return JSONResponse(content={'success': False, 'error': 'Invalid user_id'}, status_code=400)
        
        if auth_user.get('id') != user_id:
            return JSONResponse(content={'success': False, 'error': 'User mismatch'}, status_code=403)
        
        ton = (data.get('ton') or '').strip()
        if not ton or (not ton.startswith('UQ') and not ton.startswith('EQ')):
            return JSONResponse(content={'success': False, 'error': 'Invalid TON wallet format'}, status_code=400)
        
        db.set_ton(user_id, ton)
        return JSONResponse(content={'success': True, 'message': 'TON wallet saved'})
    except Exception as e:
        return JSONResponse(content={'success': False, 'error': str(e)}, status_code=500)

async def handle_currency_rates(request):
    try:
        rates = await currency_api.fetch_rates("RUB")
        return JSONResponse(content={
            'success': True,
            'rates': rates,
            'source': getattr(currency_api, 'last_source', 'unknown'),
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return JSONResponse(content={
            'success': False,
            'error': str(e),
            'rates': currency_api.get_stale_cache('RUB'),
            'source': getattr(currency_api, 'last_source', 'unknown')
        }, status_code=500)

PREMIUM_PRICES = {30: 299, 45: 419, 60: 559, 90: 799, 365: 2999}

async def handle_activate_ref_code(request):
    try:
        auth_user = await get_authenticated_webapp_user(request)
        if not auth_user:
            return JSONResponse(content={'success': False, 'error': 'Unauthorized Mini App request'}, status_code=401)

        data = await request.json()
        user_id = int(data.get('user_id') or 0)
        code = data.get('code', '').strip().upper()

        if auth_user.get('id') != user_id:
            return JSONResponse(content={'success': False, 'error': 'User mismatch'}, status_code=403)
        if not user_id or not code:
            return JSONResponse(content={'success': False, 'error': 'Missing params'}, status_code=400)

        # Сначала пробуем как промокод друга
        success_friend, result_friend = db.use_friend_promocode(code, user_id)
        if success_friend:
            return JSONResponse(content={
                'success': True,
                'bonus': result_friend['bonus'],
                'message': f'✅ Промокод друга активирован! Вы получили {result_friend["bonus"]} RUB'
            })

        success, result = db.use_promocode(code, user_id)
        if success:
            return JSONResponse(content={
                'success': True,
                'bonus': result,
                'message': f'✅ Промокод активирован! Получено {result} RUB'
            })
        return JSONResponse(content={'success': False, 'error': result or '❌ Код не найден'})
    except Exception as e:
        logger.error(f"activate_ref_code error: {e}")
        return JSONResponse(content={'success': False, 'error': str(e)}, status_code=500)

async def handle_buy_premium(request):
    try:
        auth_user = await get_authenticated_webapp_user(request)
        if not auth_user:
            return JSONResponse(content={'success': False, 'error': 'Unauthorized'}, status_code=401)

        data = await request.json()
        user_id = int(data.get('user_id', 0))
        if auth_user.get('id') != user_id:
            return JSONResponse(content={'success': False, 'error': 'User mismatch'}, status_code=403)

        days = int(data.get('days', 30))
        
        if not user_id:
            return JSONResponse(content={'success': False, 'error': 'Missing user_id'}, status_code=400)
        
        user = db.get_user(user_id)
        if not user:
            return JSONResponse(content={'success': False, 'error': 'User not found'}, status_code=404)
        
        if db.is_premium(user_id):
            return JSONResponse(content={'success': False, 'error': 'Premium already active'})
        
        if days not in PREMIUM_PRICES:
            return JSONResponse(content={'success': False, 'error': 'Invalid duration'}, status_code=400)
        
        price_rub = PREMIUM_PRICES[days]
        
        rates = await currency_api.fetch_rates("RUB")
        
        async with db_batch_lock:
            balances = {}
            for curr in ["RUB", "BYN", "UAH", "KZT", "UZS", "EUR", "USD", "TON", "USDT", "STARS"]:
                balances[curr] = db.get_balance(user_id, curr)
            
            total_rub = 0
            for curr, amount in balances.items():
                if curr == "RUB":
                    total_rub += amount
                else:
                    rate = rates.get(curr, 0)
                    if rate > 0:
                        total_rub += amount * rate
            
            if total_rub < price_rub:
                return JSONResponse(content={
                    'success': False,
                    'error': 'Недостаточно средств. Пополните баланс через поддержку.',
                    'total_rub': round(total_rub, 2),
                    'price_rub': price_rub
                })
            
            deduction_order = ["RUB", "USD", "EUR", "USDT", "TON", "STARS", "BYN", "UAH", "KZT", "UZS"]
            remaining = price_rub
            deducted = {}
            
            for curr in deduction_order:
                if remaining <= 0:
                    break
                bal = balances.get(curr, 0)
                if bal <= 0:
                    continue
                
                if curr == "RUB":
                    deduct = min(bal, remaining)
                    db.update_balance(user_id, curr, -deduct)
                    deducted[curr] = deduct
                    remaining -= deduct
                else:
                    rate = rates.get(curr, 0)
                    if rate <= 0:
                        continue
                    needed = remaining / rate
                    if bal >= needed:
                        deduct_amount = needed
                        db.update_balance(user_id, curr, -deduct_amount)
                        deducted[curr] = round(deduct_amount, 2)
                        remaining = 0
                    else:
                        rub_value = bal * rate
                        db.update_balance(user_id, curr, -bal)
                        deducted[curr] = bal
                        remaining -= rub_value
            
            if remaining > 0:
                return JSONResponse(content={'success': False, 'error': 'Transaction failed'}, status_code=500)
            
            db.set_premium(user_id, days, 0)
        
        # Send notification
        try:
            await bot.send_message(
                user_id,
                f"🎉 *Premium подписка активирована!*\n\n"
                f"📅 Длительность: {days if days != 365 else '365'} дн.\n"
                f"💰 Списано: {price_rub} RUB\n"
                f"💱 Списанные валюты: {', '.join(f'{v:.2f} {k}' for k, v in deducted.items())}\n\n"
                f"✨ Комиссия при сделках и выводе: 0%",
                parse_mode="Markdown"
            )
        except:
            pass
        
        return JSONResponse(content={
            'success': True,
            'days': days,
            'price_rub': price_rub,
            'deducted': deducted
        })
    except Exception as e:
        logger.error(f"buy_premium error: {e}")
        return JSONResponse(content={'success': False, 'error': str(e)}, status_code=500)


async def get_user_balances_text(user_id: int) -> str:
    text = ""
    for code, info in CURRENCIES.items():
        bal = db.get_balance(user_id, code)
        text += f"• {bal:.2f} {info['symbol']} ({code})\n"
    return text


# ========== TON/USDT БЛОКЧЕЙН МОНИТОР ==========

async def fetch_ton_transactions(address: str, limit: int = 50) -> list:
    if not TON_API_KEY:
        logger.warning("TON_API_KEY not configured, skipping blockchain monitor")
        return []
    try:
        url = f"{TON_API_BASE}/getTransactions"
        params = {"address": address, "limit": limit, "api_key": TON_API_KEY}
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    logger.warning(f"TON API returned {resp.status}")
                    return []
                data = await resp.json()
                if not data.get("ok"):
                    return []
                return data.get("result", [])
    except Exception as e:
        logger.warning(f"TON API fetch error: {e}")
        return []


async def poll_ton_payments():
    while True:
        try:
            await asyncio.sleep(TON_POLL_INTERVAL_SECONDS)
            if not TON_ESCROW_ADDRESS:
                continue

            txs = await fetch_ton_transactions(TON_ESCROW_ADDRESS)
            if not txs:
                continue

            last_processed_tx = db.get_service_state("last_ton_tx_hash", "")

            for tx in txs:
                tx_hash = tx.get("hash", "")
                if not tx_hash or tx_hash == last_processed_tx:
                    continue

                in_msg = tx.get("in_msg", {})
                if not in_msg:
                    continue

                source = in_msg.get("source", "")
                value_nano = int(in_msg.get("value", "0"))
                value_ton = value_nano / 1e9

                comment_hex = in_msg.get("msg_data", {}).get("body", "")
                comment = ""
                if comment_hex and len(comment_hex) > 8:
                    try:
                        raw = bytes.fromhex(comment_hex[4:]) if len(comment_hex) > 4 else b""
                        if raw:
                            comment = raw.decode("utf-8", errors="ignore").strip("\x00").strip()
                    except Exception:
                        comment = ""

                async with deal_lock:
                    db.cursor.execute(
                        "SELECT id, status, payment_comment, amount, buyer, currency, seller FROM deals "
                        "WHERE status IN ('awaiting', 'payment_pending') AND payment_method = 'ton'"
                    )
                    pending = db.cursor.fetchall()

                    for deal_row in pending:
                        deal_id = deal_row[0]
                        deal_status = deal_row[1]
                        deal_comment = deal_row[2] or ""
                        deal_amount = float(deal_row[3] or 0)
                        buyer_id = deal_row[4]
                        currency = deal_row[5]
                        seller_id = deal_row[6]

                        if comment != deal_comment:
                            continue
                        if currency == "TON" and abs(value_ton - deal_amount) > 0.01:
                            continue
                        tx_amount = value_ton if currency == "TON" else value_ton

                        if db.has_ton_payment(tx_hash):
                            continue

                        db.add_ton_payment(tx_hash, deal_id, buyer_id, currency, tx_amount, comment, source)
                        db.mark_deal_paid_onchain(deal_id, tx_hash)
                        db.set_service_state("last_ton_tx_hash", tx_hash)
                        logger.info(f"TON payment confirmed: deal #{deal_id}, tx {tx_hash}")

                        if buyer_id:
                            asyncio.create_task(notify_user(
                                buyer_id,
                                f"✅ *On-chain оплата подтверждена!*\n\nСделка #{deal_id}\nСумма: {tx_amount} {currency}"
                            ))
                        asyncio.create_task(notify_user(
                            seller_id,
                            f"💰 *Сделка #{deal_id} оплачена через блокчейн!*\n\nПередайте товар покупателю."
                        ))
                        break

        except asyncio.CancelledError:
            logger.info("TON monitor cancelled")
            break
        except Exception as e:
            logger.error(f"TON monitor error: {e}")
            await asyncio.sleep(TON_POLL_INTERVAL_SECONDS)


async def verify_ton_usdt_payment(deal_id: int, tx_hash: str) -> dict:
    if not TON_API_KEY:
        return {"verified": False, "error": "TON API key not configured"}
    try:
        url = f"{TON_API_BASE}/getTransaction"
        params = {"hash": tx_hash, "api_key": TON_API_KEY}
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return {"verified": False, "error": f"API returned {resp.status}"}
                data = await resp.json()
                if not data.get("ok") or not data.get("result"):
                    return {"verified": False, "error": "Transaction not found"}
                tx = data["result"]
                in_msg = tx.get("in_msg", {})
                value_nano = int(in_msg.get("value", "0"))
                value_ton = value_nano / 1e9
                source = in_msg.get("source", "")
                return {
                    "verified": True,
                    "tx_hash": tx_hash,
                    "amount_ton": value_ton,
                    "source": source,
                    "timestamp": tx.get("utime", 0)
                }
    except Exception as e:
        return {"verified": False, "error": str(e)}


# ========== API: АДМИНИСТРИРОВАНИЕ СПОРОВ ==========

async def get_admin_or_deny(request) -> Optional[Dict]:
    auth_user = await get_authenticated_webapp_user(request)
    if not auth_user:
        return None
    user_id = auth_user.get("id")
    if user_id not in ADMIN_IDS:
        return None
    return auth_user


async def handle_admin_disputes(request):
    admin = await get_admin_or_deny(request)
    if not admin:
        return JSONResponse(content={'error': 'Unauthorized'}, status_code=401)
    disputes = db.get_disputes()
    result = []
    for d in disputes:
        result.append({
            'dispute_id': d[0],
            'deal_id': d[1],
            'opened_by': d[2],
            'reason': d[3],
            'status': d[4],
            'created_at': str(d[5])
        })
    return JSONResponse(content={'disputes': result})


async def handle_admin_resolve_dispute(request):
    admin = await get_admin_or_deny(request)
    if not admin:
        return JSONResponse(content={'success': False, 'error': 'Unauthorized'}, status_code=401)

    try:
        data = await request.json()
        dispute_id = int(data.get('dispute_id', 0))
        decision = data.get('decision', '')

        if not dispute_id or decision not in ('seller', 'buyer'):
            return JSONResponse(content={'success': False, 'error': 'Invalid params'}, status_code=400)

        async with deal_lock:
            result = db.resolve_dispute_with_decision(dispute_id, decision)

        if not result:
            return JSONResponse(content={'success': False, 'error': 'Dispute already resolved or invalid'})

        if result['decision'] == 'seller':
            asyncio.create_task(notify_user(
                result['seller_id'],
                f"✅ *Спор по сделке #{result['deal_id']} решён в вашу пользу.*\n\nНачислено {fmt_num(result['seller_amount'])} {result['currency']}"
            ))
            if result.get('buyer_id'):
                asyncio.create_task(notify_user(
                    result['buyer_id'],
                    f"⚖️ *Спор по сделке #{result['deal_id']} закрыт.*\n\nРешение: выплата продавцу."
                ))
        else:
            if result.get('buyer_id'):
                asyncio.create_task(notify_user(
                    result['buyer_id'],
                    f"↩️ *Спор по сделке #{result['deal_id']} решён в вашу пользу.*\n\nВозврат: {fmt_num(result['amount'])} {result['currency']}"
                ))
            asyncio.create_task(notify_user(
                result['seller_id'],
                f"⚖️ *Спор по сделке #{result['deal_id']} закрыт.*\n\nРешение: возврат покупателю."
            ))

        return JSONResponse(content={
            'success': True,
            'deal_id': result['deal_id'],
            'decision': decision,
            'status': result['status']
        })
    except Exception as e:
        return JSONResponse(content={'success': False, 'error': str(e)}, status_code=500)


# ========== API: ОПЛАТА/ПОДТВЕРЖДЕНИЕ СДЕЛОК (Mini App) ==========

async def handle_pay_deal(request):
    auth_user = await get_authenticated_webapp_user(request)
    if not auth_user:
        return JSONResponse(content={'success': False, 'error': 'Unauthorized'}, status_code=401)

    try:
        data = await request.json()
        user_id = int(data.get('user_id', 0))
        if auth_user.get('id') != user_id:
            return JSONResponse(content={'success': False, 'error': 'User mismatch'}, status_code=403)

        did = int(data.get('deal_id', 0))
        if not did:
            return JSONResponse(content={'success': False, 'error': 'Missing deal_id'}, status_code=400)

        async with deal_lock:
            deal = db.get_deal(did)
            if not deal or deal.get("status") not in ("awaiting", "payment_pending"):
                return JSONResponse(content={'success': False, 'error': 'Deal not available for payment'})

            if deal["seller"] == user_id:
                return JSONResponse(content={'success': False, 'error': 'Cannot pay own deal'})

            if deal.get("buyer") and deal["buyer"] != user_id:
                return JSONResponse(content={'success': False, 'error': 'Deal locked to another buyer'})

            currency = deal["currency"]
            amount = float(deal["amount"])
            payment_method = deal.get("payment_method") or ("ton" if currency in ("TON", "USDT") else "internal")

            if payment_method == "ton":
                payment_comment = deal.get("payment_comment") or generate_payment_comment(did, user_id)
                payment_address = deal.get("payment_address") or TON_ESCROW_ADDRESS
                if not payment_address:
                    return JSONResponse(content={'success': False, 'error': 'TON escrow not configured'})

                payment_amount = quantize_amount(deal.get("payment_amount") or amount, "0.000001")
                started = db.start_external_payment(did, user_id, payment_comment, payment_address, payment_amount)
                if not started:
                    return JSONResponse(content={'success': False, 'error': 'Could not initiate on-chain payment'})

                return JSONResponse(content={
                    'success': True,
                    'method': 'ton',
                    'payment_address': payment_address,
                    'payment_comment': payment_comment,
                    'payment_amount': payment_amount,
                    'currency': currency,
                    'deal_id': did
                })

            if not db.update_balance(user_id, currency, -amount):
                balance = db.get_balance(user_id, currency)
                return JSONResponse(content={'success': False, 'error': f'Insufficient funds. Balance: {balance} {currency}'})

            if not deal.get("buyer"):
                db.set_buyer(did, user_id)
            db.upd_deal_status(did, "paid")

        asyncio.create_task(notify_user(
            deal["seller"],
            f"💰 *Сделка #{did} оплачена!*\n\n🎁 {escape_md(deal['item'])}\n💰 {fmt_num(amount)} {currency}\n\nПокупатель внёс средства. Передайте товар."
        ))

        return JSONResponse(content={'success': True, 'method': 'internal', 'deal_id': did})
    except Exception as e:
        logger.exception(f"pay_deal error: {e}")
        return JSONResponse(content={'success': False, 'error': str(e)}, status_code=500)


async def handle_mark_sent(request):
    auth_user = await get_authenticated_webapp_user(request)
    if not auth_user:
        return JSONResponse(content={'success': False, 'error': 'Unauthorized'}, status_code=401)

    try:
        data = await request.json()
        user_id = int(data.get('user_id', 0))
        if auth_user.get('id') != user_id:
            return JSONResponse(content={'success': False, 'error': 'User mismatch'}, status_code=403)

        did = int(data.get('deal_id', 0))
        async with deal_lock:
            deal = db.get_deal(did)
            if not deal or deal.get("status") != "paid":
                return JSONResponse(content={'success': False, 'error': 'Deal not paid'})
            if deal["seller"] != user_id:
                return JSONResponse(content={'success': False, 'error': 'Not the seller'})

            db.upd_deal_status(did, "item_sent")

        asyncio.create_task(notify_user(
            deal["buyer"],
            f"📦 *Товар передан!*\n\nСделка #{did}\n✅ Подтвердите получение"
        ))

        return JSONResponse(content={'success': True, 'deal_id': did})
    except Exception as e:
        return JSONResponse(content={'success': False, 'error': str(e)}, status_code=500)


async def handle_confirm_receipt(request):
    auth_user = await get_authenticated_webapp_user(request)
    if not auth_user:
        return JSONResponse(content={'success': False, 'error': 'Unauthorized'}, status_code=401)

    try:
        data = await request.json()
        user_id = int(data.get('user_id', 0))
        if auth_user.get('id') != user_id:
            return JSONResponse(content={'success': False, 'error': 'User mismatch'}, status_code=403)

        did = int(data.get('deal_id', 0))
        async with deal_lock:
            deal = db.get_deal(did)
            if not deal or deal.get("status") != "item_sent":
                return JSONResponse(content={'success': False, 'error': 'Item not sent yet'})
            if deal["buyer"] != user_id:
                return JSONResponse(content={'success': False, 'error': 'Not the buyer'})

            seller_id = deal["seller"]
            amount = float(deal["amount"])
            currency = deal["currency"]
            commission = db.get_user_commission(seller_id, "deal")
            commission_amount = quantize_amount(amount * commission)
            seller_amount = quantize_amount(amount - commission_amount)

            db.update_balance(seller_id, currency, seller_amount)
            db.upd_deal_status(did, "completed")
            referral_bonus = db.credit_referral_commission(seller_id, did, currency, commission_amount)

        premium_text = " (без комиссии)" if commission == 0 else ""
        asyncio.create_task(notify_user(
            seller_id,
            f"✅ *Сделка #{did} завершена!*\n\n💰 Получено: {fmt_num(seller_amount)} {currency}{premium_text}"
        ))
        asyncio.create_task(notify_user(
            user_id,
            f"✅ *Сделка #{did} успешно завершена!*\n\nСредства переведены продавцу."
        ))

        if referral_bonus:
            asyncio.create_task(notify_user(
                referral_bonus['referrer_id'],
                f"👥 *Реферальный бонус*\n\nПо сделке #{did} начислено {referral_bonus['reward']} RUB (10% от сервисной комиссии)."
            ))

        return JSONResponse(content={'success': True, 'deal_id': did, 'seller_amount': seller_amount, 'currency': currency})
    except Exception as e:
        return JSONResponse(content={'success': False, 'error': str(e)}, status_code=500)

# ========== API: GET_PROFILE (для нового фронтенда) ==========

async def handle_get_profile(request):
    """GET /api/get_profile?tg_id={id} — alias для /api/user, но с tg_id."""
    auth_user = await get_authenticated_webapp_user(request)
    if not auth_user:
        return JSONResponse(content={'error': 'Unauthorized Mini App request'}, status_code=401)

    user_id = int(request.query_params.get('tg_id', 0) or request.query_params.get('user_id', 0))
    if auth_user.get('id') != user_id:
        return JSONResponse(content={'error': 'User mismatch'}, status_code=403)

    user = db.get_user_dict(user_id)
    if not user:
        return JSONResponse(content={'error': 'User not found'}, status_code=404)

    balances = {}
    for curr in ['RUB', 'BYN', 'UAH', 'KZT', 'UZS', 'EUR', 'USD', 'TON', 'USDT', 'STARS']:
        balances[curr] = db.get_balance(user_id, curr)

    referral_code = user.get('referral_code') or 'novix'
    deals = db.get_user_deals(user_id)
    deals_list = [build_deal_payload(db.get_deal(d[0]), user_id) for d in deals[:20]]

    try:
        rates = await currency_api.fetch_rates('RUB')
    except Exception:
        rates = currency_api.get_stale_cache('RUB')

    return JSONResponse(content={
        'id': user['user_id'],
        'username': user['username'],
        'firstName': user['username'] or 'User',
        'balances': balances,
        'rates': rates,
        'rates_source': getattr(currency_api, 'last_source', 'unknown'),
        'is_premium': db.is_premium(user_id),
        'rating': user.get('rating', 0) or 0,
        'card': user.get('card_details') or '',
        'ton': user.get('ton') or user.get('ton_wallet') or '',
        'deals': deals_list,
        'referral_code': referral_code,
        'referral_count': db.get_referral_count(user_id),
        'referral_earnings': db.get_referral_earnings(user_id),
        'is_admin': user_id in ADMIN_IDS,
        'premium_until': db.get_premium_info(user_id).get('expires') if db.is_premium(user_id) else None,
        'ton_escrow_enabled': bool(TON_ESCROW_ADDRESS and TON_API_KEY)
    })


# ========== API: GET_DEALS (только сделки) ==========

async def handle_get_deals(request):
    """GET /api/get_deals?tg_id={id} — реальные сделки из БД, без хардкода."""
    auth_user = await get_authenticated_webapp_user(request)
    if not auth_user:
        return JSONResponse(content={'error': 'Unauthorized Mini App request'}, status_code=401)

    user_id = int(request.query_params.get('tg_id', 0) or request.query_params.get('user_id', 0))
    if auth_user.get('id') != user_id:
        return JSONResponse(content={'error': 'User mismatch'}, status_code=403)

    deals = db.get_user_deals(user_id)
    deals_list = [build_deal_payload(db.get_deal(d[0]), user_id) for d in deals]

    return JSONResponse(content={'success': True, 'deals': deals_list})


# ========== API: SAVE_BILLING (карта + TON одним запросом) ==========

async def handle_save_billing(request):
    """POST /api/save_billing — сохраняет карту и/или TON кошелёк."""
    auth_user = await get_authenticated_webapp_user(request)
    if not auth_user:
        return JSONResponse(content={'success': False, 'error': 'Unauthorized Mini App request'}, status_code=401)

    try:
        data = await request.json()
        user_id = int(data.get('tg_id', 0) or data.get('user_id', 0))
        if auth_user.get('id') != user_id:
            return JSONResponse(content={'success': False, 'error': 'User mismatch'}, status_code=403)

        card_number = data.get('card_number', '').replace(' ', '')
        ton_wallet = (data.get('ton_wallet') or '').strip()

        if card_number:
            if not card_number.isdigit() or len(card_number) not in (16, 19):
                return JSONResponse(content={'success': False, 'error': 'Invalid card format'}, status_code=400)
            db.set_card(user_id, card_number)

        if ton_wallet:
            if not ton_wallet.startswith('UQ') and not ton_wallet.startswith('EQ'):
                return JSONResponse(content={'success': False, 'error': 'Invalid TON wallet format'}, status_code=400)
            db.set_ton(user_id, ton_wallet)

        return JSONResponse(content={'success': True})
    except Exception as e:
        return JSONResponse(content={'success': False, 'error': str(e)}, status_code=500)


# ========== API: ACTIVATE_PROMO (промокоды/реф-коды) ==========

async def handle_activate_promo(request):
    """POST /api/activate_promo — активация промокода или реферального кода."""
    try:
        auth_user = await get_authenticated_webapp_user(request)
        if not auth_user:
            return JSONResponse(content={'success': False, 'error': 'Unauthorized Mini App request'}, status_code=401)

        data = await request.json()
        user_id = int(data.get('tg_id', 0) or data.get('user_id', 0))
        promo_code = data.get('promo_code', '').strip().upper()

        if auth_user.get('id') != user_id:
            return JSONResponse(content={'success': False, 'error': 'User mismatch'}, status_code=403)
        if not user_id or not promo_code:
            return JSONResponse(content={'success': False, 'error': 'Missing params'}, status_code=400)

        referrer_id = db.get_user_by_referral_code(promo_code)
        if referrer_id:
            if referrer_id == user_id:
                return JSONResponse(content={'success': False, 'error': 'Нельзя активировать свой собственный код'})
            user = db.get_user_dict(user_id)
            if user and user.get('referred_by') is not None:
                return JSONResponse(content={'success': False, 'error': 'Вы уже активировали реферальный код'})
            db.cursor.execute("UPDATE users SET referred_by = ? WHERE user_id = ?", (referrer_id, user_id))
            db.update_balance(referrer_id, "RUB", 50)
            db.update_balance(user_id, "RUB", 50)
            db.add_notification(referrer_id, "🎉 Пользователь активировал ваш реферальный код! +50 RUB")
            db.add_notification(user_id, "🎉 Добро пожаловать! +50 RUB за активацию реферального кода")
            db.conn.commit()
            return JSONResponse(content={
                'success': True,
                'bonus': 50,
                'message': '✅ Реферальный код активирован! Вы получили 50 RUB'
            })

        success, result = db.use_promocode(promo_code, user_id)
        if success:
            return JSONResponse(content={
                'success': True,
                'bonus': result,
                'message': f'✅ Промокод активирован! Получено {result} RUB'
            })
        return JSONResponse(content={'success': False, 'error': result or '❌ Код не найден'})
    except Exception as e:
        logger.error(f"activate_promo error: {e}")
        return JSONResponse(content={'success': False, 'error': str(e)}, status_code=500)


# ========== FASTAPI APP ==========

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.path.exists(IMAGES_PATH):
        os.makedirs(IMAGES_PATH)

    await bot.delete_webhook(drop_pending_updates=True)

    polling_task = asyncio.create_task(dp.start_polling(bot))
    ton_task = asyncio.create_task(poll_ton_payments())

    yield

    polling_task.cancel()
    ton_task.cancel()
    try:
        await polling_task
    except:
        pass
    try:
        await ton_task
    except:
        pass
    await bot.session.close()

fastapi_app = FastAPI(title="Novix Gift Bot API", lifespan=lifespan)

fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://heyken777.github.io",  # Ваш фронтенд на GitHub Pages
        "https://methodology-identifies-number-dash.trycloudflare.com",  # Ваш туннель
        "http://localhost:8000",  # Локальная разработка
        "http://127.0.0.1:8000"  # Локальная разработка
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

fastapi_app.add_api_route("/api/user", handle_api, methods=["GET"])
fastapi_app.add_api_route("/api/get_profile", handle_get_profile, methods=["GET"])
fastapi_app.add_api_route("/api/get_deals", handle_get_deals, methods=["GET"])
fastapi_app.add_api_route("/api/create_deal", handle_create_deal, methods=["POST"])
fastapi_app.add_api_route("/api/save_card", handle_save_card, methods=["POST"])
fastapi_app.add_api_route("/api/save_ton", handle_save_ton, methods=["POST"])
fastapi_app.add_api_route("/api/save_billing", handle_save_billing, methods=["POST"])
fastapi_app.add_api_route("/api/currency/rates", handle_currency_rates, methods=["GET"])
fastapi_app.add_api_route("/api/achievements", handle_achievements, methods=["GET"])
fastapi_app.add_api_route("/api/claim_achievement", handle_claim_achievement, methods=["POST"])
fastapi_app.add_api_route("/api/buy_premium", handle_buy_premium, methods=["POST"])
fastapi_app.add_api_route("/api/activate_ref_code", handle_activate_ref_code, methods=["POST"])
fastapi_app.add_api_route("/api/activate_promo", handle_activate_promo, methods=["POST"])
fastapi_app.add_api_route("/api/admin/disputes", handle_admin_disputes, methods=["GET"])
fastapi_app.add_api_route("/api/admin/resolve_dispute", handle_admin_resolve_dispute, methods=["POST"])
fastapi_app.add_api_route("/api/pay_deal", handle_pay_deal, methods=["POST"])
fastapi_app.add_api_route("/api/mark_sent", handle_mark_sent, methods=["POST"])
fastapi_app.add_api_route("/api/confirm_receipt", handle_confirm_receipt, methods=["POST"])

frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
if os.path.isdir(frontend_dir):
    fastapi_app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
else:
    logger.warning("Папка frontend/ не найдена — статические файлы не будут раздаваться")