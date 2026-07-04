from users.views import OWNER_TELEGRAM_ID


def ceo_context(request):
    tid = request.session.get('telegram_id')
    if tid == OWNER_TELEGRAM_ID:
        return {
            'user_name': 'Heyken',
            'user_username': '@Arkadiex',
            'user_role': 'CEO / Владелец',
            'premium_tier': 'VIP',
            'is_ceo': True,
        }
    return {}
