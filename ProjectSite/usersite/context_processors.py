from django.conf import settings


def usersite_context(request):
    bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', 'NovixGiftBot')
    return {
        'bot_username': bot_username,
        'bot_link': f"https://t.me/{bot_username}",
    }
