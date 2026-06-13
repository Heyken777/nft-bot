import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(","))) if os.getenv("ADMIN_IDS") else []
SECRET_KEY = os.getenv("SECRET_KEY")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://heyken777.github.io/nft-bot")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@NovixProHelp")
MANAGER_USERNAME = os.getenv("MANAGER_USERNAME", "@NovixProHelp")
BOT_USERNAME = os.getenv("BOT_USERNAME", "NovixGift_Bot")
BOT_NAME = os.getenv("BOT_NAME", "Novix Gift")

COMMISSION_DEAL = 0.02
COMMISSION_WITHDRAW = 0.10
PREMIUM_COMMISSION_DEAL = 0.01
PREMIUM_COMMISSION_WITHDRAW = 0.05
PREMIUM_PRICE = 500

IMAGES_PATH = "images"

print("✅ Конфигурация загружена из .env")