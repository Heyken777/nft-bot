import asyncio
import sqlite3
import logging
import random
import secrets
from aiohttp import web
import json
import re
import os
from datetime import datetime, timedelta
from typing import Optional, Tuple, List
from bot_config import *
from currency_api import currency_api

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, FSInputFile, InputMediaPhoto
)

WEBAPP_URL = "https://heyken777.github.io/nft-bot"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

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
    """Редактирует или отправляет новое сообщение"""
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
    """Для обычных сообщений"""
    if photo_name and img_exists(photo_name):
        photo = FSInputFile(img_path(photo_name))
        await msg.answer_photo(photo=photo, caption=text, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await msg.answer(text, parse_mode="Markdown", reply_markup=reply_markup)

@dp.callback_query(lambda c: c.data == "referral")
async def referral_cb(call: CallbackQuery):
    user = db.get_user(call.from_user.id)
    ref_code = user[8] if len(user) > 8 and user[8] else "novix"
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{ref_code}"
    
    text = (
        f"👥 *Реферальная программа*\n\n"
        f"🔗 Твоя реферальная ссылка:\n`{ref_link}`\n\n"
        f"✨ За каждого приглашённого друга вы получите *50 RUB* на баланс!\n"
        f"💰 Друг также получит *50 RUB* за регистрацию!\n\n"
        f"📊 *Твоя статистика:*\n"
        f"• Приглашено: {db.get_referral_count(call.from_user.id)} чел.\n"
        f"• Заработано: {fmt_num(db.get_referral_earnings(call.from_user.id))} RUB"
    )
    
    await edit_or_new(call, text, back_kb(), "ПРИГЛАСИТЬ ДРУГА.jpg")
    await call.answer()

def main_kb(is_admin: bool = False):
    """Обычная клавиатура (без Mini App, для fallback)"""
    kb = [
        [InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="deposit"),
        InlineKeyboardButton(text="💸 Вывести средства", callback_data="withdraw")],
        [InlineKeyboardButton(text="💳 Моя карта", callback_data="set_card"),
        InlineKeyboardButton(text="📱 Мой TON-кошелёк", callback_data="set_ton")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
        InlineKeyboardButton(text="⭐ Premium", callback_data="buy_premium")],
        [InlineKeyboardButton(text="➕ Создать сделку", callback_data="create_deal"),
        InlineKeyboardButton(text="📋 Мои сделки", callback_data="my_deals")],
        [InlineKeyboardButton(text="👥 Рефералы", callback_data="referral")]
        [InlineKeyboardButton(text="🏆 Достижения", callback_data="achievements")],
    ]
    if is_admin:
        kb.append([InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="menu")]])


def cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]])


def deal_kb(deal_id: int, role: str, status: str):
    kb = []
    if role == "buyer" and status == "awaiting":
        kb.append([InlineKeyboardButton(text="✅ Оплатить", callback_data=f"pay_{deal_id}")])
    elif role == "seller" and status == "paid":
        kb.append([InlineKeyboardButton(text="📦 Товар передан", callback_data=f"sent_{deal_id}")])
    elif role == "buyer" and status == "item_sent":
        kb.append([InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"recv_{deal_id}")])
    kb.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def share_kb(deal_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Поделиться", switch_inline_query=f"Сделка #{deal_id} - https://t.me/{BOT_USERNAME}?start=deal_{deal_id}")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]
    ])

# ========== КОНФИГУРАЦИЯ ==========
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
    """Клавиатура выбора валюты для карты"""
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


def ton_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu")]
    ])

def premium_days_kb(user_id: int):
    """Клавиатура выбора дней Premium подписки"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 30 дней (299 RUB)", callback_data=f"premium_days_30_{user_id}"),
        InlineKeyboardButton(text="📅 45 дней (419 RUB)", callback_data=f"premium_days_45_{user_id}")],
        [InlineKeyboardButton(text="📅 60 дней (559 RUB)", callback_data=f"premium_days_60_{user_id}"),
        InlineKeyboardButton(text="📅 90 дней (799 RUB)", callback_data=f"premium_days_90_{user_id}")],
        [InlineKeyboardButton(text="👑 FOREVER (1999 RUB)", callback_data=f"premium_days_forever_{user_id}")],
        [InlineKeyboardButton(text="❌ Забрать Premium", callback_data=f"premium_remove_{user_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
    ])

def admin_currency_kb(action: str, user_id: int):
    """Клавиатура выбора валюты для зачисления/списания"""
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
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def currency_kb():
    """Клавиатура выбора валюты с флагами"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 RUB", callback_data="cur_RUB"),
        InlineKeyboardButton(text="🇺🇸 USD", callback_data="cur_USD"),
        InlineKeyboardButton(text="🇪🇺 EUR", callback_data="cur_EUR")],
        [InlineKeyboardButton(text="🇺🇦 UAH", callback_data="cur_UAH"),
        InlineKeyboardButton(text="🇰🇿 KZT", callback_data="cur_KZT"),
        InlineKeyboardButton(text="🇺🇿 UZS", callback_data="cur_UZS")],
        [InlineKeyboardButton(text="🇧🇾 BYN", callback_data="cur_BYN"),
        InlineKeyboardButton(text="💎 TON", callback_data="cur_TON"),
        InlineKeyboardButton(text="💎 USDT", callback_data="cur_USDT")],
        [InlineKeyboardButton(text="⭐ Stars", callback_data="cur_STARS")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu")]
    ])

def get_user_role(deals_count: int) -> str:
    """Возвращает роль пользователя в зависимости от количества сделок"""
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
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="menu")]
    ])

class Database:
    def __init__(self):
        self.conn = sqlite3.connect("novixgift.db")
        self.cursor = self.conn.cursor()
        self.init()

    def get_connection(self):
        return sqlite3.connect(self.db_path)

    def add_column_if_not_exists(self, table_name: str, column_name: str, column_type: str):
        """Добавляет колонку в таблицу, если её нет"""
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
        # Проверяем существующие колонки
        self.cursor.execute("PRAGMA table_info(users)")
        existing_columns = [col[1] for col in self.cursor.fetchall()]
        
        # Создаём таблицу если её нет
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
            "is_premium": "INTEGER DEFAULT 0",
            "premium_expires": "TIMESTAMP",
            "rating": "REAL DEFAULT 0",
            "reviews_count": "INTEGER DEFAULT 0",
            "referral_code": "TEXT",
            "referred_by": "INTEGER",
            "referral_earnings": "REAL DEFAULT 0",
            "notifications_enabled": "INTEGER DEFAULT 1",
            "premium_granted_by": "INTEGER DEFAULT 0",
            "premium_granted_at": "TIMESTAMP",
            "premium_duration_days": "INTEGER DEFAULT 0"
        }
        
        for col_name, col_type in columns_to_add.items():
            if col_name not in existing_columns:
                try:
                    self.cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
                    logger.info(f"Добавлена колонка {col_name}")
                except Exception as e:
                    logger.warning(f"Не удалось добавить {col_name}: {e}")
        
        # Таблица сделок
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS deals (
                deal_id INTEGER PRIMARY KEY,
                seller_id INTEGER,
                buyer_id INTEGER,
                item_name TEXT,
                amount REAL,
                status TEXT DEFAULT 'awaiting_payment',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        self.add_column_if_not_exists("users", "last_activity", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        self.add_column_if_not_exists("deals", "commission", "REAL DEFAULT 0")
        self.add_column_if_not_exists("deals", "is_featured", "INTEGER DEFAULT 0")
        self.add_column_if_not_exists("deals", "completed_at", "TIMESTAMP")
        self.add_column_if_not_exists("deals", "currency", "TEXT DEFAULT 'RUB'")
        
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
        
        # Таблица логов админов (безопасность)
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
        self.conn.commit()        
        logger.info("База данных инициализирована")    

    def get_user(self, uid):
        self.cursor.execute("SELECT * FROM users WHERE user_id = ?", (uid,))
        return self.cursor.fetchone()

    def reg_user(self, uid, name):
        if not self.get_user(uid):
            self.cursor.execute("INSERT INTO users (user_id, username) VALUES (?, ?)", (uid, name))
            self.conn.commit()
        else:
            self.cursor.execute("UPDATE users SET last_activity = CURRENT_TIMESTAMP WHERE user_id = ?", (uid,))
            self.conn.commit()

    def upd_balance(self, uid, delta):
        self.cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (delta, uid))
        self.conn.commit()

    def set_card(self, uid, card):
        self.cursor.execute("UPDATE users SET card = ? WHERE user_id = ?", (card, uid))
        self.conn.commit()

    def get_premium_users(self):
        """Возвращает список пользователей с активной Premium подпиской"""
        self.cursor.execute("""
            SELECT user_id, username, premium_until 
            FROM users 
            WHERE is_premium = 1 AND (premium_until > CURRENT_TIMESTAMP OR premium_until IS NULL)
        """)
        return self.cursor.fetchall()    

    def set_ton(self, uid, ton):
        self.cursor.execute("UPDATE users SET ton = ? WHERE user_id = ?", (ton, uid))
        self.conn.commit()

    def create_deal(self, seller, item, amount, commission, deal_id, currency="RUB"):
        allowed_currencies = ['RUB', 'BYN', 'UAH', 'KZT', 'UZS', 'EUR', 'USD', 'TON', 'USDT', 'STARS']
        if currency not in allowed_currencies:
            currency = 'RUB'
        
        self.cursor.execute("""
            INSERT INTO deals (id, seller, item, amount, commission, currency, status) 
            VALUES (?, ?, ?, ?, ?, ?, 'awaiting')
        """, (deal_id, seller, item, amount, commission, currency))
        self.conn.commit()
        return deal_id    

    def get_user_achievements(self, user_id: int) -> List[Tuple]:
        """Получает список достижений пользователя"""
        self.cursor.execute("""
        SELECT a.*, ua.earned_at, ua.claimed
        FROM achievements_list a
        LEFT JOIN user_achievements ua ON a.id = ua.achievement_id AND ua.user_id = ?
        ORDER BY ua.earned_at IS NULL, a.reward DESC
        """, (user_id,))
        return self.cursor.fetchall()

    def check_and_award_achievements(self, user_id: int, stats: dict):
        """Проверяет и выдаёт достижения"""
        self.cursor.execute("SELECT id, requirement_type, requirement_value, reward FROM achievements_list")
        achievements = self.cursor.fetchall()
        
        earned = []
        for ach_id, req_type, req_value, reward in achievements:
            # Проверяем, не получено ли уже достижение
            self.cursor.execute("SELECT id FROM user_achievements WHERE user_id = ? AND achievement_id = ?", (user_id, ach_id))
            if self.cursor.fetchone():
                continue
            
            # Проверяем условие
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
        """Забирает награду за достижение"""
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
        """Собирает статистику пользователя для проверки достижений"""
        # Завершённые сделки
        self.cursor.execute("SELECT COUNT(*) FROM deals WHERE (seller_id = ? OR buyer_id = ?) AND status = 'completed'", (user_id, user_id))
        completed_deals = self.cursor.fetchone()[0]
        
        # Сделки в роли продавца
        self.cursor.execute("SELECT COUNT(*) FROM deals WHERE seller_id = ? AND status = 'completed'", (user_id,))
        sales = self.cursor.fetchone()[0]
        
        # Количество приглашённых
        self.cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
        referrals = self.cursor.fetchone()[0]
        
        # Баланс в TON
        ton_balance = self.get_balance(user_id, 'TON')
        
        # Количество покупок Premium
        self.cursor.execute("SELECT COUNT(*) FROM user_achievements WHERE user_id = ? AND achievement_id = 'premium_starter'", (user_id,))
        premium_count = self.cursor.fetchone()[0]
        
        return {
            'completed_deals': completed_deals,
            'sales': sales,
            'referrals': referrals,
            'ton_balance': ton_balance,
            'premium_count': premium_count
        }
    
    def get_balance(self, uid, currency):
        """Получает баланс в указанной валюте"""
        col_name = f"balance_{currency}"
        self.cursor.execute(f"SELECT {col_name} FROM users WHERE user_id = ?", (uid,))
        row = self.cursor.fetchone()
        return row[0] if row else 0

    def get_user_by_referral_code(self, ref_code: str):
        """Получает пользователя по реферальному коду"""
        self.cursor.execute("SELECT user_id FROM users WHERE referral_code = ?", (ref_code,))
        row = self.cursor.fetchone()
        return row[0] if row else None    

    def update_balance(self, uid, currency, delta):
        """Обновляет баланс в указанной валюте"""
        col_name = f"balance_{currency}"
        self.cursor.execute(f"UPDATE users SET {col_name} = {col_name} + ? WHERE user_id = ?", (delta, uid))
        self.conn.commit()    

    def get_all_balances(self, uid):
        """Получает все балансы пользователя"""
        balances = {}
        for curr in ["RUB", "BYN", "UAH", "KZT", "UZS", "EUR", "USD", "TON", "USDT", "STARS"]:
            balances[curr] = self.get_balance(uid, curr)
        return balances    

    def get_card_currency(self, uid):
        """Получает валюту карты пользователя"""
        self.cursor.execute("SELECT card_currency FROM users WHERE user_id = ?", (uid,))
        row = self.cursor.fetchone()
        return row[0] if row else "RUB"    

    def set_card(self, uid, card, currency="RUB"):
        """Сохраняет карту с валютой"""
        self.cursor.execute("UPDATE users SET card_details = ?, card_currency = ? WHERE user_id = ?", (card, currency, uid))
        self.conn.commit()

    def get_deal(self, did):
        self.cursor.execute("SELECT * FROM deals WHERE id = ?", (did,))
        row = self.cursor.fetchone()
        if row:
            # Приводим к единому формату с правильными индексами
            return {
                "id": row[0],
                "seller": row[1],
                "buyer": row[2],
                "item": row[3],
                "amount": row[4],
                "commission": row[5],
                "currency": row[6] if len(row) > 6 else "RUB",
                "status": row[7] if len(row) > 7 else "awaiting",
                "created": row[8] if len(row) > 8 else None,
                "completed": row[9] if len(row) > 9 else None
            }
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
        self.cursor.execute("SELECT user_id, username, balance, is_premium FROM users ORDER BY user_id LIMIT ? OFFSET ?", (limit, offset))
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

    def add_dispute(self, did, uid, reason):
        self.cursor.execute("INSERT INTO disputes (deal_id, user_id, reason) VALUES (?, ?, ?)", (did, uid, reason))
        self.conn.commit()

    def get_disputes(self):
        self.cursor.execute("SELECT d.*, dl.item FROM disputes d JOIN deals dl ON d.deal_id = dl.id WHERE d.status = 'pending'")
        return self.cursor.fetchall()

    def resolve_dispute(self, did):
        self.cursor.execute("UPDATE disputes SET status = 'resolved' WHERE id = ?", (did,))
        self.conn.commit()

    def set_premium(self, user_id: int, days: int, granted_by: int):
        """Выдача Premium подписки"""
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
        """Забрать Premium подписку"""
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

    def get_premium_info(self, user_id: int) -> dict:
        """Получить информацию о Premium подписке"""
        self.cursor.execute("""
            SELECT is_premium, premium_until, premium_granted_by, premium_granted_at, premium_duration_days
            FROM users WHERE user_id = ?
        """, (user_id,))
        row = self.cursor.fetchone()
        if row and row[0] == 1:
            return {
                "active": True,
                "expires": row[1],
                "granted_by": row[2],
                "granted_at": row[3],
                "duration_days": row[4]
            }
            return {"active": False}

    def is_premium(self, uid):
        self.cursor.execute("SELECT is_premium, premium_until FROM users WHERE user_id = ?", (uid,))
        row = self.cursor.fetchone()
        if row and row[0] == 1:
            if row[1] is None:
                return True  # FOREVER
            try:
                expires = datetime.strptime(row[1], '%Y-%m-%d %H:%M:%S')
                if datetime.now() < expires:
                    return True
            except:
                return True
        return False

    def add_notification(self, user_id: int, text: str):
        """Добавляет уведомление пользователю"""
        self.cursor.execute("INSERT INTO notifications (user_id, text) VALUES (?, ?)", (user_id, text))
        self.conn.commit()

    def get_unread(self, uid):
        self.cursor.execute("SELECT id, text FROM notifications WHERE user_id = ? AND read = 0", (uid,))
        return self.cursor.fetchall()

    def mark_read(self, uid):
        self.cursor.execute("UPDATE notifications SET read = 1 WHERE user_id = ?", (uid,))
        self.conn.commit()

    def get_all_users_for_mailing(self):
        self.cursor.execute("SELECT user_id FROM users")
        return [row[0] for row in self.cursor.fetchall()]

    def get_stats(self):
        self.cursor.execute("SELECT COUNT(*) FROM users")
        users = self.cursor.fetchone()[0]
        self.cursor.execute("SELECT SUM(balance) FROM users")
        balance = self.cursor.fetchone()[0] or 0
        self.cursor.execute("SELECT COUNT(*) FROM deals WHERE status = 'completed'")
        completed = self.cursor.fetchone()[0]
        self.cursor.execute("SELECT COUNT(*) FROM deals WHERE status NOT IN ('completed', 'cancelled')")
        active = self.cursor.fetchone()[0]
        self.cursor.execute("SELECT COUNT(*) FROM disputes WHERE status = 'pending'")
        disputes = self.cursor.fetchone()[0]
        return {"users": users, "balance": balance, "completed": completed, "active": active, "disputes": disputes}


db = Database()

class CreateDealState(StatesGroup):
    currency = State()  # НОВОЕ - выбор валюты
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

@dp.message(Command("start"))
async def start_cmd(msg: types.Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    name = msg.from_user.username or "Unknown"

    # Разбираем параметры после /start
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

    # Регистрация пользователя
    db.reg_user(uid, name)
    
    # Обработка реферального кода
    if ref_code:
        referrer_id = db.get_user_by_referral_code(ref_code)
        if referrer_id and referrer_id != uid:
            db.update_balance(referrer_id, "RUB", 50)
            db.update_balance(uid, "RUB", 50)
            db.add_notification(referrer_id, f"🎉 Пользователь @{name} перешёл по вашей реферальной ссылке! Вам начислено 50 RUB")
            db.add_notification(uid, "🎉 Добро пожаловать! Вам начислено 50 RUB за регистрацию по реферальной ссылке")
            logger.info(f"Реферальный бонус: {referrer_id} -> {uid}")

    is_admin = uid in ADMIN_IDS
    WEBAPP_URL = "https://heyken777.github.io/nft-bot"

    # ========== ОБРАБОТКА ССЫЛКИ НА СДЕЛКУ ==========
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

    # ========== ОТКРЫТИЕ MINI APP ==========
    if startapp_page:
        # Определяем, какую страницу открыть
        page_urls = {
            "home": f"{WEBAPP_URL}/index.html",
            "profile": f"{WEBAPP_URL}/profile.html",
            "deals": f"{WEBAPP_URL}/deals.html",
            "create_deal": f"{WEBAPP_URL}/create_deal.html",
            "premium": f"{WEBAPP_URL}/premium.html",
            "referral": f"{WEBAPP_URL}/referral.html",
            "buy_premium": f"{WEBAPP_URL}/buy_premium.html",
            "privacy": f"{WEBAPP_URL}/privacy.html",
            "terms": f"{WEBAPP_URL}/terms.html"
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
                [InlineKeyboardButton(text="📱 Открыть Mini App", web_app=types.WebAppInfo(url=web_app_url))],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")]
            ])
        )
        return

    # ========== ОБЫЧНОЕ ГЛАВНОЕ МЕНЮ ==========
    text = f"🎁 *Добро пожаловать в {BOT_NAME}!*\n\n✨ *Главное меню*"
    
    # Клавиатура с кнопкой Mini App
    main_kb_with_app = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Открыть приложение", web_app=types.WebAppInfo(url=f"{WEBAPP_URL}/index.html"))],
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
    
    # Отправка с фото или без
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


@dp.callback_query(lambda c: c.data == "buy_premium")
async def buy_premium_cb(call: CallbackQuery, state: FSMContext):
    text = "⭐ *Premium подписка*\n\nВыберите валюту для оплаты:"
    await call.message.delete()
    await call.message.answer(text, parse_mode="Markdown", reply_markup=premium_currency_kb())
    await state.set_state(BuyPremiumState.currency)
    await call.answer()


def premium_currency_kb():
    """Клавиатура выбора валюты для покупки Premium"""
    currencies = ["RUB", "USD", "EUR", "TON", "USDT", "STARS"]
    kb = []
    row = []
    for curr in currencies:
        row.append(InlineKeyboardButton(text=CURRENCIES[curr]["symbol"], callback_data=f"premium_cur_{curr}"))
        if len(row) == 3:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_user_commission(self, user_id: int, action: str = "deal") -> float:
    """Возвращает комиссию для пользователя
    action: 'deal' - комиссия со сделки, 'withdraw' - комиссия на вывод
    """
    is_premium = self.is_premium(user_id)
    if is_premium:
        return 0.0
    
    if action == "deal":
        return COMMISSION_DEAL
    else:
        return COMMISSION_WITHDRAW

@dp.callback_query(BuyPremiumState.currency)
async def premium_currency_cb(call: CallbackQuery, state: FSMContext):
    if not call.data.startswith("premium_cur_"):
        return
    
    currency = call.data.replace("premium_cur_", "")
    await state.update_data(currency=currency)
    
    # Курсы валют (можно обновлять через API позже)
    rates = {
        "RUB": 1,
        "USD": 92.5,
        "EUR": 100,
        "TON": 500,
        "USDT": 92,
        "STARS": 15
    }
    
    price_rub = {
        30: 299,
        45: 419,
        60: 559,
        90: 799,
        365: 2999
    }
    
    text = f"⭐ *Premium подписка*\n\n💰 Валюта оплаты: {currency}\n\nВыберите длительность:"
    kb = []
    
    for days, rub_price in price_rub.items():
        converted = int(rub_price / rates.get(currency, 1))
        if days == 365:
            kb.append([InlineKeyboardButton(text=f"👑 {days} дней (365 дней) - {converted} {currency}", callback_data=f"premium_buy_{days}_{currency}")])
        else:
            kb.append([InlineKeyboardButton(text=f"📅 {days} дней - {converted} {currency}", callback_data=f"premium_buy_{days}_{currency}")])
    
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="buy_premium")])
    kb.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")])
    
    await call.message.delete()
    await call.message.answer(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(BuyPremiumState.days)
    await call.answer()


@dp.callback_query(BuyPremiumState.days)
async def premium_buy_cb(call: CallbackQuery, state: FSMContext):
    if not call.data.startswith("premium_buy_"):
        return
    
    parts = call.data.split("_")
    days = int(parts[2])
    currency = parts[3]
    
    # Курсы валют
    rates = {
        "RUB": 1,
        "USD": 92.5,
        "EUR": 100,
        "TON": 500,
        "USDT": 92,
        "STARS": 15
    }
    
    price_rub = {
        30: 299,
        45: 419,
        60: 559,
        90: 799,
        365: 2999
    }
    
    price = int(price_rub[days] / rates.get(currency, 1))
    user_balance = db.get_balance(call.from_user.id, currency)
    
    if user_balance < price:
        await call.answer(f"❌ Недостаточно средств! Нужно {price} {currency}", show_alert=True)
        return
    
    # Списание средств
    db.update_balance(call.from_user.id, currency, -price)
    
    # Выдача Premium
    db.set_premium(call.from_user.id, days, 0)  # 0 = купил сам
    
    await call.message.delete()
    await call.message.answer(
        f"✅ *Premium подписка активирована!*\n\n"
        f"📅 Длительность: {days} дней\n"
        f"💰 Оплачено: {price} {currency}\n"
        f"✨ Комиссия при получении средств: *0%*\n"
        f"✨ Комиссия при выводе: *0%*",
        parse_mode="Markdown",
        reply_markup=main_kb(call.from_user.id in ADMIN_IDS)
    )
    await state.clear()
    await call.answer()


@dp.callback_query(lambda c: c.data == "menu")
async def menu_cb(call: CallbackQuery):
    uid = call.from_user.id
    is_admin = uid in ADMIN_IDS
    text = f"🎁 *{BOT_NAME}*\n\n✨ *Главное меню*"
    if img_exists("ГЛАВНОЕ МЕНЮ.jpg"):
        await call.message.edit_media(InputMediaPhoto(media=FSInputFile(img_path("ГЛАВНОЕ МЕНЮ.jpg")), caption=text, parse_mode="Markdown"), reply_markup=main_kb(is_admin))
    else:
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=main_kb(is_admin))
    await call.answer()


@dp.callback_query(lambda c: c.data == "cancel")
async def cancel_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await menu_cb(call)


@dp.callback_query(lambda c: c.data == "deposit")
async def deposit_cb(call: CallbackQuery):
    text = f"💳 *Пополнение баланса*\n\nДля пополнения напишите менеджеру:\n{MANAGER_USERNAME}\n\nУкажите ID: `{call.from_user.id}` и сумму."
    await edit_or_new(call, text, back_kb(), "ПОПОЛНИТЬ БАЛАНС.jpg")
    await call.answer()


@dp.callback_query(lambda c: c.data.startswith("premium_remove_"))
async def premium_remove_cb(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    user_id = int(call.data.split("_")[2])
    db.remove_premium(user_id)
    
    await call.message.delete()
    await call.message.answer(
        f"✅ *Premium подписка отозвана!*\n\n"
        f"👤 Пользователь: `{user_id}`",
        parse_mode="Markdown",
        reply_markup=admin_kb()
    )
    
    await bot.send_message(
        user_id,
        f"⚠️ *Ваша Premium подписка была отозвана администратором.*",
        parse_mode="Markdown"
    )
    
    await call.answer()


@dp.callback_query(lambda c: c.data == "withdraw")
async def withdraw_cb(call: CallbackQuery):
    user_id = call.from_user.id
    is_premium = db.is_premium(user_id)
    commission = db.get_user_commission(user_id, "withdraw")
    
    # Получаем общий баланс по всем валютам
    total_balance = 0
    for code in CURRENCIES.keys():
        total_balance += db.get_balance(user_id, code)
    
    if total_balance <= 0:
        text = "❌ *Недостаточно средств для вывода.*\n\nПополните баланс или дождитесь поступления средств по сделкам."
        if img_exists("ВЫВЕСТИ СРЕДСТВА.jpg"):
            await call.message.edit_media(InputMediaPhoto(media=FSInputFile(img_path("ВЫВЕСТИ СРЕДСТВА.jpg")), caption=text, parse_mode="Markdown"), reply_markup=back_kb())
        else:
            await call.message.edit_text(text, parse_mode="Markdown", reply_markup=back_kb())
        await call.answer()
        return
    
    commission_text = "0%" if commission == 0 else f"{int(commission*100)}%"
    premium_text = "\n✨ *Premium*: комиссия 0%" if commission == 0 else ""
    
    text = (
        f"💸 *Вывод средств*\n\n"
        f"💰 Ваш общий баланс: *{fmt_num(total_balance)} RUB*\n"
        f"💼 Комиссия: *{commission_text}*{premium_text}\n\n"
        f"Для вывода средств напишите менеджеру:\n{MANAGER_USERNAME}\n\n"
        f"Укажите ID: `{user_id}`, сумму и реквизиты."
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📩 Написать менеджеру", url=f"https://t.me/{MANAGER_USERNAME.replace('@', '')}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu")]
    ])
    
    await edit_or_new(call, text, kb, "ВЫВЕСТИ СРЕДСТВА.jpg")
    await call.answer()


@dp.callback_query(lambda c: c.data == "set_card")
async def set_card_cb(call: CallbackQuery, state: FSMContext):
    text = "💳 *Моя карта для вывода*\n\nВыберите валюту карты (RUB / BYN / UAH / KZT / EUR):"
    await call.message.delete()
    if img_exists("КАРТА ДЛЯ ВЫВОДА.jpg"):
        await call.message.answer_photo(photo=FSInputFile(img_path("КАРТА ДЛЯ ВЫВОДА.jpg")), caption=text, parse_mode="Markdown", reply_markup=card_currency_kb())
    else:
        await call.message.answer(text, parse_mode="Markdown", reply_markup=card_currency_kb())
    await call.answer()


@dp.callback_query(lambda c: c.data.startswith("card_cur_"))
async def set_card_currency_cb(call: CallbackQuery, state: FSMContext):
    currency = call.data.replace("card_cur_", "")
    await state.update_data(card_currency=currency)
    await call.message.delete()
    await call.message.answer(f"💳 *Введите номер карты*\n\nВалюта карты: {currency}\n\nПример: 4276 1600 1234 5678", parse_mode="Markdown", reply_markup=cancel_kb())
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
    
    # ВЫЗЫВАЕМ НОВУЮ ФУНКЦИЮ
    db.set_card(msg.from_user.id, card, currency)
    
    await state.clear()
    await msg.answer(f"✅ *Карта сохранена!*\n\n💳 {card}\n🌍 Валюта: {currency}", parse_mode="Markdown", reply_markup=back_kb())


@dp.callback_query(lambda c: c.data == "set_ton")
async def set_ton_cb(call: CallbackQuery, state: FSMContext):
    await call.message.delete()
    text = "💎 *Введите адрес TON-кошелька*\n\nПример: `UQCD39VS5jcptHL8vMjEXrzGaRcCVYtoq7BGPk2vwUCGzE`"
    if img_exists("TON.jpg"):
        await call.message.answer_photo(photo=FSInputFile(img_path("TON.jpg")), caption=text, parse_mode="Markdown", reply_markup=cancel_kb())
    else:
        await call.message.answer(text, parse_mode="Markdown", reply_markup=cancel_kb())
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

async def handle_save_card(request):
    data = await request.json()
    user_id = data.get('user_id')
    card = data.get('card')
    db.set_card(user_id, card)
    return web.json_response({'success': True})

async def handle_save_ton(request):
    data = await request.json()
    user_id = data.get('user_id')
    ton = data.get('ton')
    db.set_ton(user_id, ton)
    return web.json_response({'success': True})

@dp.callback_query(lambda c: c.data == "achievements")
async def achievements_cb(call: CallbackQuery):
    user_id = call.from_user.id
    achievements = db.get_user_achievements(user_id)
    stats = db.get_achievement_stats(user_id)
    
    # Проверяем новые достижения
    earned = db.check_and_award_achievements(user_id, stats)
    for ach_id, reward in earned:
        await call.answer(f"🎉 Получено достижение! +{reward} RUB", show_alert=True)
    
    # Обновляем список после проверки
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

@dp.callback_query(lambda c: c.data == "profile")
async def profile_cb(call: CallbackQuery):
    user = db.get_user(call.from_user.id)
    ton = user[4] or "не указан"
    card = user[3] or "не указана"
    
    is_premium = db.is_premium(call.from_user.id)
    premium_info = db.get_premium_info(call.from_user.id)
    
    # Получаем количество завершённых сделок
    deals = db.get_user_deals(call.from_user.id)
    completed_deals = len([d for d in deals if d[6] == "completed"])
    
    # Получаем роль пользователя
    role = get_user_role(completed_deals)
    
    # Формируем балансы
    balances = ""
    for code, info in CURRENCIES.items():
        bal = db.get_balance(call.from_user.id, code)
        if bal > 0:
            balances += f"• {fmt_num(bal)} {info['symbol']} ({code})\n"
    
    if not balances:
        balances = "• 0 🇷🇺 (RUB)\n"
    
    # Premium статус
    if is_premium and premium_info.get("active"):
        expires = premium_info.get("expires", "")
        if expires and expires != "FOREVER":
            exp_date = datetime.strptime(expires, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y')
            premium_status = f"✅ Активен до {exp_date}"
        else:
            premium_status = "✅ Активен (FOREVER)"
        granted_by = premium_info.get("granted_by")
        if granted_by and granted_by != 0:
            premium_status += f"\n👑 Выдал: `{granted_by}`"
    else:
        premium_status = "❌ Не активен"
    
    # Проверка, является ли пользователь администратором
    is_admin = call.from_user.id in ADMIN_IDS
    admin_badge = "👑 *Администратор*\n" if is_admin else ""
    
    text = (
        f"👤 *Личный кабинет*\n\n"
        f"{admin_badge}"
        f"🆔 Ваш ID: `{user[0]}`\n"
        f"🔗 Username: @{escape_md(user[1] or 'без username')}\n"
        f"🏆 Роль: {role}\n"
        f"📊 Сделок завершено: {completed_deals}\n\n"
        f"💼 *Баланс:*\n{balances}\n"
        f"💎 TON-кошелёк: {escape_md(ton)}\n\n"
        f"💳 Карта для вывода:\n{escape_md(card)}\n\n"
        f"⭐ *Premium статус:* {premium_status}"
    )
    
    await edit_or_new(call, text, back_kb(), "ЛИЧНЫЙ КАБИНЕТ.jpg")
    await call.answer()


@dp.callback_query(lambda c: c.data == "create_deal")
async def create_deal_cb(call: CallbackQuery, state: FSMContext):
    await call.message.delete()
    text = "🌍 *Выберите валюту для сделки:*\n\nRUB, USD, EUR, UAH, KZT, UZS, BYN, TON, USDT, Stars"
    if img_exists("СОЗДАНИЕ СДЕЛКИ.jpg"):
        await call.message.answer_photo(photo=FSInputFile(img_path("СОЗДАНИЕ СДЕЛКИ.jpg")), caption=text, parse_mode="Markdown", reply_markup=currency_kb())
    else:
        await call.message.answer(text, parse_mode="Markdown", reply_markup=currency_kb())
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
    await call.message.delete()
    
    # ОТПРАВЛЯЕМ ФОТОГРАФИЮ
    if img_exists("СОЗДАНИЕ СДЕЛКИ.jpg"):
        await call.message.answer_photo(photo=FSInputFile(img_path("СОЗДАНИЕ СДЕЛКИ.jpg")), caption=text, parse_mode="Markdown", reply_markup=cancel_kb())
    else:
        await call.message.answer(text, parse_mode="Markdown", reply_markup=cancel_kb())
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
    
    # ОТПРАВЛЯЕМ ФОТОГРАФИЮ
    if img_exists("СОЗДАНИЕ СДЕЛКИ.jpg"):
        await msg.answer_photo(photo=FSInputFile(img_path("СОЗДАНИЕ СДЕЛКИ.jpg")), caption=text, parse_mode="Markdown", reply_markup=cancel_kb())
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
    
    # Генерируем 6-значный ID
    import random
    deal_id = random.randint(100000, 999999)
    while db.get_deal(deal_id):
        deal_id = random.randint(100000, 999999)
    
    # Получаем комиссию (для Premium = 0%)
    commission = db.get_user_commission(msg.from_user.id, "deal")
    
    # Создаём сделку
    db.create_deal(msg.from_user.id, item, price, commission, deal_id, currency)
    
    link = f"https://t.me/{BOT_USERNAME}?start=deal_{deal_id}"
    
    text = (
        f"✅ *Сделка успешно создана!*\n\n"
        f"🧾 ID: `{deal_id}`\n"
        f"📦 Товар: {escape_md(item)}\n"
        f"💰 Цена: {fmt_num(price)} {currency}\n"
        f"💼 Комиссия: {int(commission*100)}%\n\n"
        f"🔗 Отправьте покупателю ссылку:\n"
        f"`{link}`\n\n"
        f"Или нажмите *Поделиться сделкой* ниже."
    )
    
    await state.clear()
    
    if img_exists("СДЕЛКА УСПЕШНО СОЗДАНА.jpg"):
        await msg.answer_photo(photo=FSInputFile(img_path("СДЕЛКА УСПЕШНО СОЗДАНА.jpg")), caption=text, parse_mode="Markdown", reply_markup=share_kb(deal_id))
    else:
        await msg.answer(text, parse_mode="Markdown", reply_markup=share_kb(deal_id))


@dp.callback_query(lambda c: c.data == "my_deals")
async def my_deals_cb(call: CallbackQuery):
    deals = db.get_user_deals(call.from_user.id)
    await call.message.delete()
    if not deals:
        await call.message.answer("📭 У вас пока нет сделок", reply_markup=back_kb())
    else:
        text = "📋 *Мои сделки*\n\n"
        for d in deals[:10]:
            role = "🟢 Продажа" if d[1] == call.from_user.id else "🔵 Покупка"
            emoji = {"awaiting": "⏳", "paid": "💰", "item_sent": "📦", "completed": "✅", "cancelled": "❌"}.get(d[6], "❓")
            text += f"{emoji} *#{d[0]}* | {role}\n   🎁 {escape_md(d[3][:20])}\n   💰 {fmt_num(d[4])} RUB\n   📍 {d[6]}\n\n"
        await call.message.answer(text, parse_mode="Markdown", reply_markup=back_kb())
    await call.answer()


@dp.callback_query(lambda c: c.data.startswith("pay_"))
async def pay_cb(call: CallbackQuery):
    did = int(call.data[4:])
    deal = db.get_deal(did)
    
    if not deal or deal["status"] != "awaiting":
        await call.answer("❌ Сделка недоступна для оплаты", show_alert=True)
        return
    
    buyer = call.from_user.id
    seller = deal["seller"]
    
    if buyer == seller:
        await call.answer("❌ Вы не можете оплатить свою собственную сделку!", show_alert=True)
        return
    
    if deal["buyer"] and deal["buyer"] != buyer:
        await call.answer("❌ Сделка уже ожидает оплаты от другого", show_alert=True)
        return
    
    if not deal["buyer"]:
        db.set_buyer(did, buyer)
    
    currency = deal["currency"]
    amount = deal["amount"]
    buyer_balance = db.get_balance(buyer, currency)
    
    if buyer_balance < amount:
        await call.answer(f"❌ Недостаточно средств. Баланс: {fmt_num(buyer_balance)} {currency}", show_alert=True)
        return
    
    # Списание средств
    db.update_balance(buyer, currency, -amount)
    db.upd_deal_status(did, "paid")
    
    await call.answer("✅ Оплата прошла успешно!", show_alert=True)
    
    text = f"✅ *Сделка #{did} оплачена!*\n\nОжидайте передачи товара от продавца"
    if img_exists("ВАША СДЕЛКА БЫЛА ОПЛАЧЕНА.jpg"):
        await call.message.edit_media(InputMediaPhoto(media=FSInputFile(img_path("ВАША СДЕЛКА БЫЛА ОПЛАЧЕНА.jpg")), caption=text, parse_mode="Markdown"))
    else:
        await call.message.edit_text(text, parse_mode="Markdown")
    
    await bot.send_message(seller,
        f"💰 *Сделка #{did} оплачена!*\n\n"
        f"🎁 {escape_md(deal['item'])}\n"
        f"💰 {fmt_num(amount)} {currency}\n\n"
        f"Отправьте товар: @{call.from_user.username}",
        parse_mode="Markdown", reply_markup=deal_kb(did, "seller", "paid"))


@dp.callback_query(lambda c: c.data.startswith("sent_"))
async def sent_cb(call: CallbackQuery):
    did = int(call.data[5:])
    deal = db.get_deal(did)
    if not deal or deal["status"] != "paid":
        await call.answer("❌ Сделка не оплачена", show_alert=True)
        return
    if deal["seller"] != call.from_user.id:
        await call.answer("❌ Вы не продавец", show_alert=True)
        return
    db.upd_deal_status(did, "item_sent")
    await call.answer("✅ Товар передан!", show_alert=True)
    await call.message.edit_text(f"✅ *Товар передан!*\nОжидайте подтверждения", parse_mode="Markdown")
    
    await bot.send_message(
        deal["buyer"],
        f"📦 *Товар передан!*\n\nСделка #{did}\n✅ Подтвердите получение",
        parse_mode="Markdown",
        reply_markup=deal_kb(did, "buyer", "item_sent")
    )


@dp.callback_query(lambda c: c.data.startswith("recv_"))
async def receive_cb(call: CallbackQuery):
    did = int(call.data[5:])
    deal = db.get_deal(did)
    if not deal or deal[7] != "item_sent":
        await call.answer("❌ Товар не передан", show_alert=True)
        return
    if deal[2] != call.from_user.id:
        await call.answer("❌ Вы не покупатель", show_alert=True)
        return
    
    seller_id = deal[1]
    amount = deal[4]
    currency = deal[6] if len(deal) > 6 else "RUB"
    
    # Получаем комиссию для продавца (если Premium = 0%)
    commission = db.get_user_commission(seller_id, "deal")
    seller_amount = amount - amount * commission
    
    # Зачисляем продавцу (в той же валюте)
    db.update_balance(seller_id, currency, seller_amount)
    db.upd_deal_status(did, "completed")
    
    await call.answer("✅ Сделка завершена!", show_alert=True)
    await call.message.edit_text(f"✅ *Сделка #{did} завершена!*\nСпасибо!", parse_mode="Markdown")
    
    # Уведомление продавцу
    premium_text = " (без комиссии)" if commission == 0 else ""
    await bot.send_message(
        seller_id,
        f"✅ *Сделка #{did} завершена!*\n\n"
        f"💰 Получено: {fmt_num(seller_amount)} {currency}{premium_text}",
        parse_mode="Markdown"
    )


@dp.callback_query(lambda c: c.data.startswith("dispute_"))
async def dispute_cb(call: CallbackQuery, state: FSMContext):
    did = int(call.data[8:])
    await state.update_data(deal_id=did)
    await call.message.delete()
    await call.message.answer("⚠️ *Причина спора*\n\nНапишите причину (макс 500 символов):", parse_mode="Markdown", reply_markup=cancel_kb())
    await state.set_state("waiting_dispute")
    await call.answer()


@dp.message(StateFilter("waiting_dispute"))
async def dispute_msg(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    db.add_dispute(data['deal_id'], msg.from_user.id, msg.text[:500])
    for admin in ADMIN_IDS:
        await bot.send_message(admin, f"⚠️ *Новый спор*\nСделка #{data['deal_id']}\nПричина: {msg.text[:200]}", parse_mode="Markdown")
    await state.clear()
    await msg.answer("✅ *Спор открыт!*\nАдминистратор рассмотрит в течение 24 часов", parse_mode="Markdown", reply_markup=back_kb())

@dp.callback_query(lambda c: c.data.startswith("premium_days_"))
async def premium_days_cb(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    parts = call.data.split("_")
    days_str = parts[2]
    user_id = int(parts[3])
    
    if days_str == "forever":
        days = 36500  # 100 лет
        days_text = "FOREVER"
    else:
        days = int(days_str)
        days_text = f"{days} дней"
    
    # Сохраняем Premium в БД
    db.set_premium(user_id, days, call.from_user.id)
    
    await call.message.delete()
    await call.message.answer(
        f"✅ *Premium подписка выдана!*\n\n"
        f"👤 Пользователь: `{user_id}`\n"
        f"📅 Длительность: {days_text}\n"
        f"👑 Выдал: @{call.from_user.username or call.from_user.id}\n"
        f"📆 Дата выдачи: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        parse_mode="Markdown",
        reply_markup=admin_kb()
    )
    
    # Уведомляем пользователя
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

async def handle_achievements(request):
    user_id = int(request.query.get('user_id', 0))
    achievements = db.get_user_achievements(user_id)
    stats = db.get_achievement_stats(user_id)
    
    return web.json_response({
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

@dp.message(StateFilter(AdminPremiumState.user_id))
async def admin_premium_user_id(msg: types.Message, state: FSMContext):
    try:
        user_id = int(msg.text)
        if not db.get_user(user_id):
            await msg.answer("❌ Пользователь не найден", reply_markup=cancel_kb())
            return
        await state.update_data(user_id=user_id)
        
        # Показываем выбор длительности
        await msg.answer(
            f"👑 *Premium подписка для пользователя {user_id}*\n\nВыберите длительность:",
            parse_mode="Markdown",
            reply_markup=premium_days_kb(user_id)
        )
        await state.clear()
    except:
        await msg.answer("❌ Введите ID числом", reply_markup=cancel_kb())

@dp.callback_query(lambda c: c.data == "admin_panel")
async def admin_panel_cb(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("⛔ Доступ запрещен", show_alert=True)
        return
    await call.message.delete()
    if img_exists("ПАНЕЛЬ АДМИНИСТРАТОРА.jpg"):
        await call.message.answer_photo(photo=FSInputFile(img_path("ПАНЕЛЬ АДМИНИСТРАТОРА.jpg")), caption="⚙️ *Админ-панель*", parse_mode="Markdown", reply_markup=admin_kb())
    else:
        await call.message.answer("⚙️ *Админ-панель*", parse_mode="Markdown", reply_markup=admin_kb())
    await call.answer()


# Пагинация для списка пользователей
user_page = {}


@dp.callback_query(lambda c: c.data.startswith("admin_"))
async def admin_actions_cb(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("⛔ Доступ запрещен", show_alert=True)
        return

    action = call.data[6:]

    if action == "credit":
        await call.message.delete() 
        await call.message.answer("💰 *Введите ID пользователя для зачисления:*", parse_mode="Markdown", reply_markup=cancel_kb())
        await state.set_state(AdminCreditState.uid)
    elif action == "debit":
        await call.message.delete()
        await call.message.answer("💸 *Введите ID пользователя для списания:*", parse_mode="Markdown", reply_markup=cancel_kb())
        await state.set_state(AdminDebitState.uid)
    elif action == "premium":
        await call.message.delete()
        await call.message.answer("👑 *Введите ID пользователя для выдачи Premium:*", parse_mode="Markdown", reply_markup=cancel_kb())
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
        await call.message.delete()
        await call.message.answer(text, parse_mode="Markdown", reply_markup=admin_kb())
    elif action == "mailing":
        await state.set_state(AdminMailingState.title)
        await call.message.delete()
        await call.message.answer("📢 *Введите заголовок рассылки:*", parse_mode="Markdown", reply_markup=cancel_kb())
    elif action == "users":
        user_page[call.from_user.id] = 0
        await show_users_page(call, 0)
    elif action == "deals":
        deals = db.get_active_deals()
        await call.message.delete()
        if not deals:
            await call.message.answer("📭 Нет активных сделок", reply_markup=admin_kb())
        else:
            text = "📦 *Активные сделки*\n\n"
            for d in deals:
                emoji = {"awaiting": "⏳", "paid": "💰", "item_sent": "📦"}.get(d[6], "❓")
                text += f"{emoji} #{d[0]} | {escape_md(d[3][:20])} | {fmt_num(d[4])} RUB | {d[6]}\n"
            await call.message.answer(text, parse_mode="Markdown", reply_markup=admin_kb())
    elif action == "disputes":
        disputes = db.get_disputes()
        await call.message.delete()
        if not disputes:
            await call.message.answer("⚠️ Нет открытых споров", reply_markup=admin_kb())
        else:
            text = "⚠️ *Споры*\n\n"
            for d in disputes:
                text += f"📝 Спор #{d[0]} | Сделка #{d[1]} | {escape_md(d[4][:50])}\n✅ /resolve_{d[0]}\n\n"
            await call.message.answer(text, parse_mode="Markdown", reply_markup=admin_kb())
    elif action == "stats":
        s = db.get_stats()
        text = f"📊 *Статистика*\n\n👥 Пользователей: {s['users']}\n💰 Балансы: {fmt_num(s['balance'])} RUB\n✅ Завершённых сделок: {s['completed']}\n🔄 Активных сделок: {s['active']}\n⚠️ Споров: {s['disputes']}"
        await call.message.delete()
        await call.message.answer(text, parse_mode="Markdown", reply_markup=admin_kb())
    elif action == "close":
        await menu_cb(call)
        return

    await call.answer()


async def show_users_page(call: CallbackQuery, page: int):
    """Показывает страницу со списком пользователей"""
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

    # Кнопки пагинации в одном ряду
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

    await call.message.delete()
    await call.message.answer(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@dp.callback_query(lambda c: c.data.startswith("users_page_"))
async def users_page_cb(call: CallbackQuery):
    page = int(call.data.split("_")[2])
    await show_users_page(call, page)
    await call.answer()


@dp.callback_query(lambda c: c.data == "users_select")
async def users_select_cb(call: CallbackQuery):
    """Показывает кнопки с ID пользователей для выбора"""
    users = db.get_all_users(50, 0)
    if not users:
        await call.answer("Нет пользователей", show_alert=True)
        return

    kb = []
    for u in users[:20]:
        kb.append([InlineKeyboardButton(text=f"{u[0]} | @{u[1] or 'без имени'}", callback_data=f"user_info_{u[0]}")])
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")])

    await call.message.delete()
    await call.message.answer("👥 *Выберите пользователя:*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()


@dp.callback_query(lambda c: c.data.startswith("user_info_"))
async def user_info_cb(call: CallbackQuery):
    uid = int(call.data.split("_")[2])
    user = db.get_user(uid)
    premium_info = db.get_premium_info(uid)
    
    balances = ""
    for code, info in CURRENCIES.items():
        bal = db.get_balance(uid, code)
        if bal > 0:
            balances += f"• {fmt_num(bal)} {info['symbol']} ({code})\n"
    
    if not balances:
        balances = "• 0\n"
    
    premium_status = "✅ Активен" if premium_info["active"] else "❌ Не активен"
    if premium_info["active"] and premium_info.get("expires"):
        if premium_info["expires"]:
            exp_date = datetime.strptime(premium_info["expires"], '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y')
            premium_status += f" (до {exp_date})"
    
    granted_info = ""
    if premium_info.get("granted_by") and premium_info["granted_by"] != 0:
        granted_info = f"\n👑 Выдал: `{premium_info['granted_by']}`"
        if premium_info.get("granted_at"):
            granted_info += f"\n📅 Дата выдачи: {premium_info['granted_at'][:16]}"
        if premium_info.get("duration_days"):
            granted_info += f"\n📆 Длительность: {premium_info['duration_days']} дней"
    
    text = (
        f"👤 *Информация о пользователе*\n\n"
        f"🆔 ID: `{user[0]}`\n"
        f"📝 Username: @{escape_md(user[1] or 'без username')}\n"
        f"📅 Регистрация: {user[10][:16] if len(user) > 10 else '?'}\n\n"
        f"💰 *Балансы:*\n{balances}\n"
        f"⭐ *Premium:* {premium_status}{granted_info}\n\n"
        f"💳 Карта: {escape_md(user[3] or 'не указана')}\n"
        f"📱 TON: {escape_md(user[4] or 'не указан')}"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Зачислить", callback_data=f"admin_credit_user_{uid}"),
        InlineKeyboardButton(text="💸 Списать", callback_data=f"admin_debit_user_{uid}")],
        [InlineKeyboardButton(text="⭐ Выдать Premium", callback_data=f"admin_premium_user_{uid}"),
        InlineKeyboardButton(text="❌ Забрать Premium", callback_data=f"premium_remove_{uid}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="users_select")]
    ])
    
    await call.message.delete()
    await call.message.answer(text, parse_mode="Markdown", reply_markup=kb)
    await call.answer()


@dp.callback_query(lambda c: c.data.startswith("admin_credit_user_"))
async def admin_credit_user_from_info(call: CallbackQuery, state: FSMContext):
    uid = int(call.data.split("_")[3])
    await state.update_data(uid=uid)
    await state.set_state(AdminCreditState.amount)
    await call.message.delete()
    await call.message.answer(f"💰 *Введите сумму для зачисления пользователю {uid}:*", parse_mode="Markdown", reply_markup=cancel_kb())
    await call.answer()


@dp.callback_query(lambda c: c.data.startswith("admin_debit_user_"))
async def admin_debit_user_from_info(call: CallbackQuery, state: FSMContext):
    uid = int(call.data.split("_")[3])
    await state.update_data(uid=uid)
    await state.set_state(AdminDebitState.amount)
    await call.message.delete()
    await call.message.answer(f"💸 *Введите сумму для списания у пользователя {uid}:*", parse_mode="Markdown", reply_markup=cancel_kb())
    await call.answer()


@dp.callback_query(lambda c: c.data.startswith("admin_premium_user_"))
async def admin_premium_user_from_info(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("⛔ Доступ запрещен", show_alert=True)
        return
    
    user_id = int(call.data.split("_")[3])
    await call.message.delete()
    await call.message.answer(
        f"👑 *Premium подписка для пользователя {user_id}*\n\nВыберите длительность:",
        parse_mode="Markdown",
        reply_markup=premium_days_kb(user_id)
    )
    await call.answer()


@dp.message(StateFilter(AdminCreditState.uid))
async def admin_credit_uid(msg: types.Message, state: FSMContext):
    try:
        user_id = int(msg.text)
        user = db.get_user(user_id)
        if not user:
            await msg.answer("❌ Пользователь не найден", reply_markup=cancel_kb())
            return
        await state.update_data(user_id=user_id)
        
        # Показываем выбор валюты
        await msg.answer(
            f"💰 *Зачисление средств для пользователя {user_id}*\n\n"
            f"Выберите валюту для зачисления:",
            parse_mode="Markdown",
            reply_markup=admin_currency_kb("credit_amount", user_id)
        )
        await state.clear()
    except:
        await msg.answer("❌ Введите ID числом", reply_markup=cancel_kb())


@dp.message(StateFilter(AdminCreditState.amount))
async def admin_credit_amount(msg: types.Message, state: FSMContext):
    try:
        amount = int(msg.text)
        if amount <= 0:
            await msg.answer("❌ Сумма должна быть больше 0", reply_markup=cancel_kb())
            return
        data = await state.get_data()
        db.upd_balance(data['uid'], amount)
        await msg.answer(f"✅ Зачислено {fmt_num(amount)} RUB пользователю {data['uid']}")
        await state.clear()
        await bot.send_message(data['uid'], f"💰 Вам зачислено {fmt_num(amount)} RUB!")
        await menu_cb(msg)
    except:
        await msg.answer("❌ Введите число", reply_markup=cancel_kb())


@dp.message(StateFilter(AdminDebitState.uid))
async def admin_debit_uid(msg: types.Message, state: FSMContext):
    try:
        user_id = int(msg.text)
        user = db.get_user(user_id)
        if not user:
            await msg.answer("❌ Пользователь не найден", reply_markup=cancel_kb())
            return
        await state.update_data(user_id=user_id)
        
        # Показываем выбор валюты
        await msg.answer(
            f"💸 *Списание средств у пользователя {user_id}*\n\n"
            f"Балансы пользователя:\n"
            f"{await get_user_balances_text(user_id)}\n\n"
            f"Выберите валюту для списания:",
            parse_mode="Markdown",
            reply_markup=admin_currency_kb("debit_amount", user_id)
        )
        await state.clear()
    except:
        await msg.answer("❌ Введите ID числом", reply_markup=cancel_kb())

async def get_user_balances_text(user_id: int) -> str:
    """Возвращает текст со всеми балансами пользователя"""
    text = ""
    for code, info in CURRENCIES.items():
        bal = db.get_balance(user_id, code)
        text += f"• {bal:.2f} {info['symbol']} ({code})\n"
    return text

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
    
    await call.message.delete()
    await call.message.answer(
        f"💰 *Зачисление средств*\n\n"
        f"👤 Пользователь: `{user_id}`\n"
        f"💱 Валюта: {currency}\n\n"
        f"💵 *Введите сумму для зачисления:*",
        parse_mode="Markdown",
        reply_markup=cancel_kb()
    )
    await call.answer()


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
    
    await call.message.delete()
    await call.message.answer(
        f"💸 *Списание средств*\n\n"
        f"👤 Пользователь: `{user_id}`\n"
        f"💱 Валюта: {currency}\n"
        f"💰 Текущий баланс: {db.get_balance(user_id, currency)} {currency}\n\n"
        f"💵 *Введите сумму для списания:*",
        parse_mode="Markdown",
        reply_markup=cancel_kb()
    )
    await call.answer()

@dp.message(StateFilter(AdminDebitState.amount))
async def admin_debit_amount(msg: types.Message, state: FSMContext):
    try:
        amount = int(msg.text)
        if amount <= 0:
            await msg.answer("❌ Сумма должна быть больше 0", reply_markup=cancel_kb())
            return
        data = await state.get_data()
        user = db.get_user(data['uid'])
        if amount > user[2]:
            await msg.answer(f"❌ Недостаточно средств. Баланс: {fmt_num(user[2])} RUB", reply_markup=cancel_kb())
            return
        db.upd_balance(data['uid'], -amount)
        await msg.answer(f"✅ Списано {fmt_num(amount)} RUB у {data['uid']}")
        await state.clear()
        await bot.send_message(data['uid'], f"💸 С вашего баланса списано {fmt_num(amount)} RUB")
        await menu_cb(msg)
    except:
        await msg.answer("❌ Введите число", reply_markup=cancel_kb())


@dp.message(StateFilter("premium"))
async def admin_premium(msg: types.Message, state: FSMContext):
    try:
        uid = int(msg.text)
        if not db.get_user(uid):
            await msg.answer("❌ Пользователь не найден", reply_markup=cancel_kb())
            return
        db.set_premium(uid)
        await msg.answer(f"✅ Премиум выдан пользователю {uid} на 30 дней")
        await state.clear()
        await bot.send_message(uid, "🎉 *Вам выдан премиум на 30 дней!*\n\n✨ Сниженная комиссия (1% на сделки)", parse_mode="Markdown")
        await menu_cb(msg)
    except:
        await msg.answer("❌ Введите ID числом", reply_markup=cancel_kb())


@dp.message(StateFilter(AdminMailingState.title))
async def mailing_title(msg: types.Message, state: FSMContext):
    await state.update_data(title=msg.text)
    await state.set_state(AdminMailingState.text)
    await msg.answer("📝 *Введите текст рассылки:*", parse_mode="Markdown", reply_markup=cancel_kb())


@dp.message(StateFilter(AdminMailingState.text))
async def mailing_text(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    users = db.get_all_users_for_mailing()
    sent = 0
    await msg.answer("🚀 Рассылка начата...")
    for uid in users:
        try:
            await bot.send_message(uid, f"📢 *{escape_md(data['title'])}*\n\n{escape_md(msg.text)}", parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await state.clear()
    await msg.answer(f"✅ Рассылка завершена!\nОтправлено: {sent} пользователям")
    await menu_cb(msg)


async def handle_api(request):
    user_id = int(request.query.get('user_id', 0))
    user = db.get_user(user_id)
    if not user:
        return web.json_response({'error': 'User not found'}, status=404)
    
    # РЕАЛЬНЫЕ БАЛАНСЫ ИЗ БД
    balances = {}
    for curr in ['RUB', 'BYN', 'UAH', 'KZT', 'UZS', 'EUR', 'USD', 'TON', 'USDT', 'STARS']:
        balances[curr] = db.get_balance(user_id, curr)
    
    return web.json_response({
        'id': user[0],
        'username': user[1],
        'firstName': user[1] or 'User',
        'balances': balances,
        'is_premium': db.is_premium(user_id),
        'rating': user[7] or 0,
        'card': user[3] or '',
        'ton': user[4] or '',
        'deals': [],  # Здесь нужно добавить реальные сделки из БД
        'referral_count': 0,
        'referral_earnings': 0
    })

async def handle_create_deal(request):
    try:
        data = await request.json()
        user_id = data.get('user_id')
        item_name = data.get('item_name')
        price = data.get('price')
        currency = data.get('currency', 'RUB')
        
        allowed = ['RUB', 'BYN', 'UAH', 'KZT', 'UZS', 'EUR', 'USD', 'TON', 'USDT', 'STARS']
        if currency not in allowed:
            currency = 'RUB'
        
        # Проверяем пользователя
        user = db.get_user(user_id)
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)
        
        # Генерируем ID сделки
        import random
        deal_id = random.randint(100000, 999999)
        while db.get_deal(deal_id):
            deal_id = random.randint(100000, 999999)
        
        # Комиссия
        commission = db.get_user_commission(user_id, "deal")
        
        # Создаём сделку
        db.create_deal(user_id, item_name, price, commission, deal_id, currency)
        
        return web.json_response({
            'id': deal_id,
            'item': item_name,
            'amount': price,
            'currency': currency,
            'commission': commission
        })
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)

async def handle_currency_rates(request):
    """Возвращает актуальные курсы валют"""
    try:
        rates = await currency_api.fetch_rates("RUB")
        return web.json_response({
            'success': True,
            'rates': rates,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return web.json_response({
            'success': False,
            'error': str(e)
        }, status=500)

# Запуск веб-сервера для API
async def start_api():
    app = web.Application()
    app.router.add_get('/api/user', handle_api)
    app.router.add_post('/api/create_deal', handle_create_deal)
    app.router.add_post('/api/save_card', handle_save_card)
    app.router.add_post('/api/save_ton', handle_save_ton)
    app.router.add_get('/api/currency/rates', handle_currency_rates)
    app.router.add_get('/api/achievements', handle_achievements)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 8080)
    await site.start()
    logger.info("✅ API сервер запущен на порту 8080")

@dp.message(lambda m: m.text and m.text.startswith("/resolve_"))
async def resolve_cmd(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    try:
        did = int(msg.text.replace("/resolve_", ""))
        db.resolve_dispute(did)
        await msg.answer(f"✅ Спор #{did} закрыт")
    except:
        await msg.answer("❌ Используйте: /resolve_номер")
        
async def main():
    logger.info("🚀 Запуск Novix Gift Bot...")
    if not os.path.exists(IMAGES_PATH):
        os.makedirs(IMAGES_PATH)
        logger.info(f"📁 Создана папка {IMAGES_PATH}")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Бот запущен")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())