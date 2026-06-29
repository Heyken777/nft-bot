from django.db import models


class User(models.Model):
    telegram_id = models.BigIntegerField(
        primary_key=True,
        db_column='user_id',
        verbose_name='Telegram ID',
    )
    username = models.TextField(
        null=True, blank=True,
        verbose_name='Username',
    )
    first_name = models.TextField(
        null=True, blank=True,
        verbose_name='Имя',
    )
    last_name = models.TextField(
        null=True, blank=True,
        verbose_name='Фамилия',
    )
    is_admin = models.BooleanField(
        default=False,
        db_column='is_admin',
        verbose_name='Администратор',
    )
    is_blocked = models.BooleanField(
        default=False,
        verbose_name='Заблокирован',
    )
    is_premium = models.BooleanField(
        default=False,
        verbose_name='Premium',
    )
    premium_until = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Premium до',
    )
    premium_granted_by = models.BigIntegerField(
        null=True, blank=True,
        verbose_name='Premium выдан (admin ID)',
    )
    premium_granted_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Premium выдан',
    )
    premium_duration_days = models.IntegerField(
        default=0,
        verbose_name='Длительность Premium (дни)',
    )
    card_details = models.TextField(
        null=True, blank=True,
        verbose_name='Реквизиты карты',
    )
    card_currency = models.TextField(
        default='RUB',
        verbose_name='Валюта карты',
    )
    ton_wallet = models.TextField(
        null=True, blank=True,
        db_column='ton_wallet',
        verbose_name='TON кошелёк',
    )
    ton = models.TextField(
        null=True, blank=True,
        verbose_name='TON (альтернативный)',
    )
    referral_code = models.TextField(
        null=True, blank=True,
        verbose_name='Реферальный код',
    )
    referred_by = models.BigIntegerField(
        null=True, blank=True,
        verbose_name='Пригласил (Telegram ID)',
    )
    referral_earnings = models.FloatField(
        default=0,
        verbose_name='Заработано на рефералах',
    )
    rating = models.FloatField(
        default=0,
        verbose_name='Рейтинг',
    )
    reviews_count = models.IntegerField(
        default=0,
        verbose_name='Кол-во отзывов',
    )
    notifications_enabled = models.BooleanField(
        default=True,
        verbose_name='Уведомления включены',
    )
    balance_rub = models.FloatField(
        default=0, db_column='balance_RUB',
        verbose_name='Баланс RUB',
    )
    balance_usd = models.FloatField(
        default=0, db_column='balance_USD',
        verbose_name='Баланс USD',
    )
    balance_eur = models.FloatField(
        default=0, db_column='balance_EUR',
        verbose_name='Баланс EUR',
    )
    balance_byn = models.FloatField(
        default=0, db_column='balance_BYN',
        verbose_name='Баланс BYN',
    )
    balance_uah = models.FloatField(
        default=0, db_column='balance_UAH',
        verbose_name='Баланс UAH',
    )
    balance_kzt = models.FloatField(
        default=0, db_column='balance_KZT',
        verbose_name='Баланс KZT',
    )
    balance_uzs = models.FloatField(
        default=0, db_column='balance_UZS',
        verbose_name='Баланс UZS',
    )
    balance_ton = models.FloatField(
        default=0, db_column='balance_TON',
        verbose_name='Баланс TON',
    )
    balance_usdt = models.FloatField(
        default=0, db_column='balance_USDT',
        verbose_name='Баланс USDT',
    )
    balance_stars = models.FloatField(
        default=0, db_column='balance_STARS',
        verbose_name='Баланс STARS',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Зарегистрирован',
    )
    last_activity = models.DateTimeField(
        auto_now=True,
        verbose_name='Последняя активность',
    )

    class Meta:
        managed = False
        db_table = 'users'
        verbose_name = 'Пользователь'
        verbose_name_plural = 'Пользователи'
        ordering = ['-created_at']

    def __str__(self):
        return self.username or f"ID:{self.telegram_id}"
