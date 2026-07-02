from django.contrib import admin
from django.http import HttpRequest
from .models import User
from .crypto_utils import decrypt_value

OWNER_TELEGRAM_ID = 1803437347


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('telegram_id', 'username', 'is_premium', 'rating', 'created_at')
    list_filter = ('is_premium', 'created_at')
    search_fields = ('username', 'telegram_id')
    readonly_fields = ('created_at', 'last_activity')

    def get_fields(self, request: HttpRequest, obj=None):
        fields = super().get_fields(request, obj)
        if obj and request.session.get('telegram_id') == OWNER_TELEGRAM_ID:
            return list(fields) + ['_decrypted_card', '_decrypted_ton']
        return fields

    def _decrypted_card(self, obj):
        return decrypt_value(obj.card_details or '') or '—'
    _decrypted_card.short_description = 'Реквизиты карты (расш.)'

    def _decrypted_ton(self, obj):
        raw = obj.ton or obj.ton_wallet or ''
        return decrypt_value(raw) or '—'
    _decrypted_ton.short_description = 'TON кошелёк (расш.)'
