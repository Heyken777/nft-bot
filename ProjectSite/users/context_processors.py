from users.views import OWNER_TELEGRAM_ID


def ceo_context(request):
    tid = request.session.get('telegram_id')
    if tid == OWNER_TELEGRAM_ID:
        username = request.session.get('username', 'Владелец')
        return {
            'user_name': username,
            'user_username': f'@{username}',
            'user_role': 'CEO / Владелец',
            'premium_tier': 'VIP',
            'is_ceo': True,
        }
    return {}
