# 🎁 Novix Gift — безопасные сделки с NFT и цифровыми товарами

[![Telegram Bot](https://img.shields.io/badge/Telegram-@NovixGift_Bot-blue?logo=telegram)](https://t.me/NovixGift_Bot)
[![GitHub Pages](https://img.shields.io/badge/GitHub%20Pages-Mini%20App-green?logo=github)]([https://heyken777.github.io/nft-bot](https://methodology-identifies-number-dash.trycloudflare.com/))
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **Novix Gift** — это Telegram-бот и Mini-приложение для проведения безопасных сделок между продавцами и покупателями с использованием эскроу-сервиса.

## 📱 Возможности

### 🤖 Telegram Бот
- 🔐 **Эскроу-сделки** — средства блокируются до подтверждения обеими сторонами
- 💰 **Мультивалютность** — поддержка 10 валют (RUB, USD, EUR, TON, USDT, STARS и др.)
- ⭐ **Premium подписка** — комиссия 0% на сделки и вывод
- 👥 **Реферальная программа** — приглашай друзей и получай бонусы
- 📊 **Админ-панель** — управление пользователями, сделками, премиум-подписками
- 🖼️ **Неоновый дизайн** — киберпанк-стиль с анимациями

### 📱 Telegram Mini App
- 🌐 **Адаптивный интерфейс** — работает на любых мобильных устройствах
- 💱 **Переключение валют** — мгновенный просмотр баланса в любой валюте
- 📝 **Создание сделок** — пошаговое создание с выбором валюты
- 👤 **Профиль пользователя** — балансы, реквизиты, статистика
- ⭐ **Покупка Premium** — выбор длительности и валюты оплаты
- 👥 **Реферальная программа** — приглашение друзей через ссылку

## 🚀 Быстрый старт

### Установка и запуск бота

```bash
# Клонируем репозиторий
git clone https://github.com/Heyken777/nft-bot.git
cd nft-bot

# Устанавливаем зависимости
pip install -r requirements.txt

# Создаём файл .env с токеном
echo BOT_TOKEN=ваш_токен > .env
echo ADMIN_IDS=ваш_id >> .env

# Запускаем бота
python bot.py
