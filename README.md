# 🎁 Novix Gift — безопасные сделки с NFT и цифровыми товарами

[![Telegram Bot](https://img.shields.io/badge/Telegram-@NovixGift_Bot-blue?logo=telegram)](https://t.me/NovixGift_Bot)
[![WebSite](https://img.shields.io/badge/WebSite-Heyken-green?logo=google-chrome)](https://heyken.ru)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **Novix Gift** — Telegram-бот + Django-сайт для безопасных сделок между продавцами и покупателями с использованием эскроу-сервиса. Мультивалютность, Premium-подписки, 2FA, P2P-переводы, админ-панель и аналитика.

---

## 📱 Возможности

### 🤖 Telegram Бот
- **Эскроу-сделки** — средства блокируются до подтверждения обеими сторонами
- **Мультивалютность** — 10 валют: RUB, USD, EUR, BYN, UAH, KZT, UZS, TON, USDT, STARS
- **P2P-переводы** — переводы между пользователями напрямую, с 2FA-подтверждением
- **2FA** — двухфакторная аутентификация для подтверждения выводов, сделок, P2P и смены реквизитов
- **Premium-подписка** — сниженная комиссия (tiers: free/premium/platinum/vip)
- **Реферальная программа** — приглашай друзей и получай бонусы
- **Уведомления** — Telegram + in-app центр уведомлений + WebSocket real-time
- **Автоэскалация сделок** — напоминания через N часов, создание тикета поддержки при длительном ожидании
- **CEO-контроль** — крупные выводы и P2P-переводы требуют ручного подтверждения владельца
- **Новые устройства** — уведомление при входе с неизвестного IP/браузера
- **Защита от брутфорса** — rate-limiting slowapi на FastAPI-эндпоинты + django-ratelimit

### 🌐 Сайт (Django — usersite + admin-panel)
- **Telegram Mini App** — адаптивный интерфейс для мобильных
- **Личный кабинет** — профиль, балансы, сделки, выводы, уведомления
- **Настройки профиля** — смена пароля, email, username, аватара
- **Платёжные реквизиты** — карта и TON-кошелёк (смена через 2FA)
- **Журнал безопасности** — лог всех sensitive-действий пользователя
- **P2P-переводы** — интерфейс отправки/получения с preview
- **Premium-магазин** — выбор тарифа и валюты оплаты
- **Тикеты поддержки** — создание, ответы, закрытие
- **Отзывы** — рейтинг продавцов/покупателей
- **Админ-панель** — управление пользователями, сделками, выводами, тикетами, спорами, промокодами, рассылками
- **Аналитика** — GMV, новые/активные пользователи, Premium-конверсия, тикеты

### 🛡️ Безопасность
- **2FA** — подтверждение sensitive-операций через Telegram inline-кнопки
- **User Audit Log** — логирование всех изменений профиля (пароль, 2FA, реквизиты, аватар)
- **Known Devices** — отслеживание входов с новых устройств
- **CEO Approval Queue** — крупные суммы (>10 000 RUB) требуют ручного одобрения
- **Rate Limiting** — slowapi (FastAPI) + django-ratelimit на критичных эндпоинтах
- **Bruteforce Protection** — блокировка IP после 5 неудачных попыток за 15 минут
- **Balance Ledger** — полный аудит всех движений средств

---

## 🏗️ Архитектура

```
┌─────────────────────────────────────────────────────┐
│                    nft-bot/                          │
│                                                      │
│  ├── bot.py              Telegram-бот (aiogram 3)    │
│  │                       + FastAPI (:9207)           │
│  │                       + APScheduler               │
│  │                       + slowapi rate-limiting     │
│  │                       + WebSocket (/ws)           │
│  │                                                    │
│  ├── ProjectSite/        Django-сайт                 │
│  │   ├── novix_admin/    Админ-панель                │
│  │   ├── users/          Пользователи/admin views    │
│  │   ├── usersite/       Личный кабинет (Mini App)   │
│  │   ├── tickets/        Тикеты поддержки            │
│  │   ├── disputes/       Споры                       │
│  │   ├── news/           Новости + партнёрства       │
│  │   ├── payments/       Платежи                     │
│  │   ├── api/            API                         │
│  │   ├── escrow/         Эскроу                      │
│  │   └── templates/      Шаблоны (админ + usersite)  │
│  │                                                    │
│  ├── bot_config.py       Конфигурация                 │
│  ├── currency_api.py     Курсы валют                 │
│  ├── crypto.py           Шифрование                   │
│  ├── id_generator.py     Генерация кодов             │
│  │                                                    │
│  └── novixgift.db        SQLite (общая БД)           │
│                                                      │
│  Балансовый учёт:                                     │
│  ├── balance_ledger (PostgreSQL/SQLite)               │
│  └── LedgerRouter (Django)                           │
└─────────────────────────────────────────────────────┘
```

### 🔗 Интеграция
- **Bot ↔ Django**: через FastAPI-эндпоинты на `BACKEND_URL:9207`
- **Django → Bot**: `POST /api/internal/{action}` — 2FA, уведомления, нотификации о новых устройствах
- **WebSocket**: real-time уведомления в Mini App через `/ws/{user_id}`
- **Общая БД**: SQLite `novixgift.db` — обе части читают/пишут в одни таблицы

---

## 🚀 Быстрый старт

### 1. Клонирование и установка

```bash
git clone https://github.com/Heyken777/nft-bot.git
cd nft-bot
pip install -r requirements.txt
```

### 2. Настройка .env

```env
BOT_TOKEN=токен_бота_от_BotFather
OWNER_TELEGRAM_ID=1803437347
BACKEND_URL=http://localhost:9207
WEBAPP_URL=http://localhost:8000

DJANGO_SECRET_KEY=сгенерируйте_случайный
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1

COMMISSION_DEAL=0.02
COMMISSION_WITHDRAW=0.10
DEAL_REMINDER_HOURS=24
DEAL_ESCALATION_HOURS=72
CEO_APPROVAL_THRESHOLD_RUB=10000

PG_ENABLED=False
```

### 3. Запуск

```bash
# Django (порт 8000)
cd ProjectSite && python manage.py runserver 0.0.0.0:8000

# Telegram-бот (порт 9207)
python bot.py
```

### 4. Создание суперпользователя

```bash
cd ProjectSite && python manage.py createsuperuser
# Telegram ID: 1803437343
```

---

## ⚙️ Конфигурация (`bot_config.py`)

| Параметр | По умолчанию | Описание |
|----------|-------------|----------|
| `COMMISSION_DEAL` | `0.02` | Комиссия со сделки (2%) |
| `COMMISSION_WITHDRAW` | `0.10` | Комиссия на вывод (10%) |
| `PREMIUM_COMMISSION_DEAL` | `0.01` | Комиссия для Premium (1%) |
| `PREMIUM_COMMISSION_WITHDRAW` | `0.05` | Комиссия вывода для Premium (5%) |
| `PREMIUM_PRICE` | `500` | Стоимость Premium (RUB) |
| `DEAL_REMINDER_HOURS` | `24` | Через сколько часов напомнить о подтверждении |
| `DEAL_ESCALATION_HOURS` | `72` | Через сколько часов создать тикет поддержки |
| `CEO_APPROVAL_THRESHOLD_RUB` | `10000` | Порог для ручного подтверждения CEO |

---

## 📊 Маршруты

### Сайт (Django)
| Путь | Описание |
|------|----------|
| `/` → `/usersite/` | Лендинг |
| `/login/` | Вход в админ-панель |
| `/dashboard/` | Дашборд |
| `/analytics/` | Аналитика |
| `/users/` | Пользователи |
| `/deals/` | Сделки |
| `/withdrawals/` | Выводы |
| `/tickets/` | Тикеты поддержки |
| `/disputes/` | Споры |
| `/ledger/` | Balance Ledger |
| `/audit/` | Audit лог |

### Usersite (Mini App)
| Путь | Описание |
|------|----------|
| `/usersite/profile/` | Профиль |
| `/usersite/settings/` | Настройки |
| `/usersite/settings/security/` | Журнал безопасности |
| `/usersite/send/` | P2P-перевод |
| `/usersite/deal/create/` | Создание сделки |
| `/usersite/withdraw/` | Вывод средств |
| `/usersite/tickets/` | Тикеты |
| `/usersite/notifications/` | Уведомления |
| `/usersite/premium/` | Premium |

### API (FastAPI, порт 9207)
| Путь | Описание |
|------|----------|
| `GET /api/user` | Профиль пользователя |
| `POST /api/create_deal` | Создание сделки (10/min) |
| `POST /api/pay_deal` | Оплата сделки (10/min) |
| `POST /api/confirm_receipt` | Подтверждение получения (10/min) |
| `POST /api/buy_premium` | Покупка Premium (5/min) |
| `POST /api/2fa/request` | Запрос 2FA-кода (5/min) |
| `POST /api/send_admin_login_code` | Код входа в админку (5/min) |
| `POST /api/internal/notify` | Отправка уведомления |
| `POST /api/internal/2fa-request` | 2FA из Django |
| `POST /api/internal/notify-new-login` | Уведомление о новом устройстве |
| `WS /ws/{user_id}` | WebSocket (real-time) |

---

## 🗄️ Основные таблицы БД

| Таблица | Назначение |
|---------|-----------|
| `users` | Пользователи, балансы, Premium, реквизиты |
| `deals` | Сделки (эскроу) |
| `balance_ledger` | Полный аудит движений средств |
| `pending_verifications` | 2FA-запросы (nonce, код, статус) |
| `withdrawal_requests` | Заявки на вывод |
| `support_tickets` | Тикеты поддержки |
| `notifications` | Telegram-уведомления |
| `usersite_notifications` | In-app уведомления |
| `auth_codes` | Коды для входа на сайт |
| `audit_logs` | Audit-лог админ-действий |
| `admin_audit_log` | Audit-лог из бота |
| `user_audit_log` | Лог действий пользователя |
| `known_devices` | Известные устройства/IP |
| `ceo_approval_queue` | Очередь подтверждений CEO |
| `reconciliation_log` | Лог сверки балансов |

---

## 📈 Версии

| Версия | Что добавили |
|--------|-------------|
| v5.23.0 | Аналитика, rate-limiting |
| v5.22.0 | CEO-одобрение крупных операций |
| v5.21.0 | Уведомления о новых устройствах |
| v5.20.0 | Журнал безопасности пользователя |
| v5.19.0 | Автоэскалация сделок |
| v5.18.0 | 2FA для смены реквизитов |
| v5.17.0 | Сверка балансов (reconciliation) |
| v5.16.0 | P2P-переводы |
| v5.15.0 | Первая стабильная версия |
