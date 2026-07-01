from django.db import models


class Review(models.Model):
    id = models.AutoField(primary_key=True, db_column='id')
    deal_id = models.IntegerField(verbose_name='ID сделки')
    reviewer_id = models.BigIntegerField(verbose_name='Кто оставил (Telegram ID)')
    reviewed_id = models.BigIntegerField(verbose_name='Кому оставили (Telegram ID)')
    rating = models.IntegerField(verbose_name='Оценка (1-5)')
    comment = models.TextField(null=True, blank=True, verbose_name='Текст отзыва')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата')
    is_moderated = models.BooleanField(default=False, verbose_name='Промодерировано')
    moderated_by = models.BigIntegerField(null=True, blank=True, verbose_name='Модератор (Telegram ID)')
    moderated_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата модерации')
    reported = models.BooleanField(default=False, verbose_name='Пожаловались')
    report_reason = models.TextField(null=True, blank=True, verbose_name='Причина жалобы')

    class Meta:
        managed = False
        db_table = 'reviews'
        unique_together = ('deal_id', 'reviewer_id')
        verbose_name = 'Отзыв'
        verbose_name_plural = 'Отзывы'
        ordering = ['-created_at']

    def __str__(self):
        return f"#{self.id} deal:{self.deal_id} ⭐{self.rating}"


class User(models.Model):
    telegram_id = models.BigIntegerField(
        primary_key=True, db_column='user_id',
        verbose_name='Telegram ID',
    )
    username = models.TextField(null=True, blank=True, verbose_name='Username')
    is_admin = models.BooleanField(default=False, db_column='is_admin', verbose_name='Администратор')
    is_premium = models.BooleanField(default=False, verbose_name='Premium')
    premium_until = models.DateTimeField(null=True, blank=True, verbose_name='Premium до')
    premium_granted_by = models.BigIntegerField(null=True, blank=True, verbose_name='Premium выдан (admin ID)')
    premium_granted_at = models.DateTimeField(null=True, blank=True, verbose_name='Premium выдан')
    premium_duration_days = models.IntegerField(default=0, verbose_name='Длительность Premium (дни)')
    card_details = models.TextField(null=True, blank=True, verbose_name='Реквизиты карты')
    card_currency = models.TextField(default='RUB', verbose_name='Валюта карты')
    ton_wallet = models.TextField(null=True, blank=True, db_column='ton_wallet', verbose_name='TON кошелёк')
    ton = models.TextField(null=True, blank=True, verbose_name='TON (альтернативный)')
    referral_code = models.TextField(null=True, blank=True, verbose_name='Реферальный код')
    referred_by = models.BigIntegerField(null=True, blank=True, verbose_name='Пригласил (Telegram ID)')
    referral_earnings = models.FloatField(default=0, verbose_name='Заработано на рефералах')
    rating = models.FloatField(default=0, verbose_name='Рейтинг')
    reviews_count = models.IntegerField(default=0, verbose_name='Кол-во отзывов')
    notifications_enabled = models.BooleanField(default=True, verbose_name='Уведомления включены')
    balance_rub = models.FloatField(default=0, db_column='balance_RUB', verbose_name='Баланс RUB')
    balance_usd = models.FloatField(default=0, db_column='balance_USD', verbose_name='Баланс USD')
    balance_eur = models.FloatField(default=0, db_column='balance_EUR', verbose_name='Баланс EUR')
    balance_byn = models.FloatField(default=0, db_column='balance_BYN', verbose_name='Баланс BYN')
    balance_uah = models.FloatField(default=0, db_column='balance_UAH', verbose_name='Баланс UAH')
    balance_kzt = models.FloatField(default=0, db_column='balance_KZT', verbose_name='Баланс KZT')
    balance_uzs = models.FloatField(default=0, db_column='balance_UZS', verbose_name='Баланс UZS')
    balance_ton = models.FloatField(default=0, db_column='balance_TON', verbose_name='Баланс TON')
    balance_usdt = models.FloatField(default=0, db_column='balance_USDT', verbose_name='Баланс USDT')
    balance_stars = models.FloatField(default=0, db_column='balance_STARS', verbose_name='Баланс STARS')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Зарегистрирован')
    last_activity = models.DateTimeField(auto_now=True, verbose_name='Последняя активность')

    class Meta:
        managed = False
        db_table = 'users'
        verbose_name = 'Пользователь'
        verbose_name_plural = 'Пользователи'
        ordering = ['-created_at']

    def __str__(self):
        return self.username or f"ID:{self.telegram_id}"


class Transaction(models.Model):
    id = models.AutoField(primary_key=True, db_column='id')
    user_id = models.BigIntegerField(verbose_name='Telegram ID')
    amount = models.FloatField(verbose_name='Сумма')
    currency = models.TextField(default='RUB', verbose_name='Валюта')
    tx_type = models.TextField(db_column='type', verbose_name='Тип (deposit/withdrawal/transfer/fee/referral)')
    description = models.TextField(null=True, blank=True, verbose_name='Описание')
    related_id = models.BigIntegerField(null=True, blank=True, verbose_name='ID связанной записи (deal и т.д.)')
    created_at = models.DateTimeField(auto_now_add=True, db_column='created_at', verbose_name='Дата')

    class Meta:
        managed = False
        db_table = 'transactions'
        verbose_name = 'Транзакция'
        verbose_name_plural = 'Транзакции'
        ordering = ['-created_at']

    def __str__(self):
        return f"#{self.id} {self.tx_type} {self.amount} {self.currency} user:{self.user_id}"