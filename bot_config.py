import os
from dotenv import load_dotenv
from currency_api import currency_api

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(","))) if os.getenv("ADMIN_IDS") else []
SECRET_KEY = os.getenv("SECRET_KEY")
WEBAPP_URL = os.getenv("WEBAPP_URL", "http://93.115.101.179:9207")
BACKEND_URL = os.getenv("BACKEND_URL", "")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@NovixProHelp")
MANAGER_USERNAME = os.getenv("MANAGER_USERNAME", "@NovixProHelp")
BOT_USERNAME = os.getenv("BOT_USERNAME", "NovixGift_Bot")
BOT_NAME = os.getenv("BOT_NAME", "Novix Gift")

WEBAPP_ORIGIN = os.getenv("WEBAPP_ORIGIN", WEBAPP_URL.rstrip('/'))
MINI_APP_AUTH_MAX_AGE = int(os.getenv("MINI_APP_AUTH_MAX_AGE", "300"))
TON_API_BASE = os.getenv("TON_API_BASE", "https://toncenter.com/api/v2")
TON_API_KEY = os.getenv("TON_API_KEY")
TON_ESCROW_ADDRESS = os.getenv("TON_ESCROW_ADDRESS", "")
TON_PAYMENT_TTL_MINUTES = int(os.getenv("TON_PAYMENT_TTL_MINUTES", "30"))
TON_POLL_INTERVAL_SECONDS = int(os.getenv("TON_POLL_INTERVAL_SECONDS", "30"))
REFERRAL_COMMISSION_SHARE = float(os.getenv("REFERRAL_COMMISSION_SHARE", "0.10"))

COMMISSION_DEAL = 0.02
COMMISSION_WITHDRAW = 0.10
PREMIUM_COMMISSION_DEAL = 0.01
PREMIUM_COMMISSION_WITHDRAW = 0.05
PREMIUM_PRICE = 500

IMAGES_PATH = "images"
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")

print("✅ Конфигурация загружена из .env")