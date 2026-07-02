from django.db import models


class Promocode(models.Model):
    code = models.TextField(primary_key=True, verbose_name='Код')
    amount = models.FloatField(default=0, verbose_name='Сумма')
    max_uses = models.IntegerField(default=1, verbose_name='Макс. использований')
    used_count = models.IntegerField(default=0, db_column='used_count', verbose_name='Использовано')
    expires_at = models.TextField(null=True, blank=True, verbose_name='Истекает')
    active = models.BooleanField(default=True, verbose_name='Активен')
    created_by = models.BigIntegerField(null=True, blank=True, verbose_name='Создатель (Telegram ID)')
    created_at = models.DateTimeField(null=True, blank=True, verbose_name='Создан')
    deleted_by = models.BigIntegerField(null=True, blank=True, verbose_name='Удалён (Telegram ID)')
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name='Удалён')
    delete_reason = models.TextField(null=True, blank=True, verbose_name='Причина удаления')

    class Meta:
        managed = False
        db_table = 'promocodes'
        verbose_name = 'Промокод'
        verbose_name_plural = 'Промокоды'

    def __str__(self):
        return self.code


class AdminLog(models.Model):
    id = models.BigIntegerField(primary_key=True, verbose_name='ID')
    user_id = models.BigIntegerField(null=True, blank=True, verbose_name='Пользователь (Telegram ID)')
    username = models.TextField(null=True, blank=True, verbose_name='Имя')
    action_type = models.TextField(null=True, blank=True, verbose_name='Тип действия')
    description = models.TextField(null=True, blank=True, verbose_name='Описание')
    ip_address = models.TextField(null=True, blank=True, verbose_name='IP')
    timestamp = models.DateTimeField(null=True, blank=True, verbose_name='Время')

    class Meta:
        managed = False
        db_table = 'audit_logs'
        verbose_name = 'Лог аудита'
        verbose_name_plural = 'Логи аудита'
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.action_type} @ {self.timestamp}"


class Deal(models.Model):
    id = models.BigIntegerField(primary_key=True, verbose_name='ID')
    seller = models.BigIntegerField(null=True, blank=True, verbose_name='Продавец')
    buyer = models.BigIntegerField(null=True, blank=True, verbose_name='Покупатель')
    item = models.TextField(null=True, blank=True, verbose_name='Товар')
    amount = models.FloatField(default=0, verbose_name='Сумма')
    commission = models.FloatField(null=True, blank=True, verbose_name='Комиссия')
    currency = models.TextField(default='RUB', verbose_name='Валюта')
    status = models.TextField(default='awaiting', verbose_name='Статус')
    created = models.DateTimeField(null=True, blank=True, verbose_name='Создана')
    completed = models.DateTimeField(null=True, blank=True, verbose_name='Завершена')
    payment_method = models.TextField(default='internal', verbose_name='Способ оплаты')
    payment_comment = models.TextField(null=True, blank=True, verbose_name='Комментарий оплаты')
    payment_address = models.TextField(null=True, blank=True, verbose_name='Адрес оплаты')
    payment_amount = models.FloatField(null=True, blank=True, verbose_name='Сумма оплаты')
    paid_tx_hash = models.TextField(null=True, blank=True, verbose_name='TX Hash')
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name='Оплачено')
    disputed_at = models.DateTimeField(null=True, blank=True, verbose_name='В споре с')

    class Meta:
        managed = False
        db_table = 'deals'
        verbose_name = 'Сделка'
        verbose_name_plural = 'Сделки'
        ordering = ['-created']

    def __str__(self):
        return f"#{self.id} {self.item}"


class Dispute(models.Model):
    id = models.BigIntegerField(primary_key=True, verbose_name='ID')
    deal_id = models.BigIntegerField(null=True, blank=True, verbose_name='Сделка')
    opened_by = models.BigIntegerField(null=True, blank=True, verbose_name='Открыт пользователем')
    reason = models.TextField(null=True, blank=True, verbose_name='Причина')
    status = models.TextField(default='pending', verbose_name='Статус')
    created_at = models.DateTimeField(null=True, blank=True, verbose_name='Создан')

    class Meta:
        managed = False
        db_table = 'disputes'
        verbose_name = 'Спор'
        verbose_name_plural = 'Споры'


class Notification(models.Model):
    id = models.BigIntegerField(primary_key=True, verbose_name='ID')
    user_id = models.BigIntegerField(null=True, blank=True, verbose_name='Пользователь')
    title = models.TextField(null=True, blank=True, verbose_name='Заголовок')
    message = models.TextField(null=True, blank=True, verbose_name='Сообщение')
    is_read = models.BooleanField(default=False, verbose_name='Прочитано')
    created_at = models.DateTimeField(null=True, blank=True, verbose_name='Создано')

    class Meta:
        managed = False
        db_table = 'notifications'
        verbose_name = 'Уведомление'
        verbose_name_plural = 'Уведомления'


class Review(models.Model):
    id = models.BigIntegerField(primary_key=True, verbose_name='ID')
    deal_id = models.BigIntegerField(null=True, blank=True, verbose_name='Сделка')
    from_user_id = models.BigIntegerField(null=True, blank=True, verbose_name='Автор отзыва')
    to_user_id = models.BigIntegerField(null=True, blank=True, verbose_name='Получатель отзыва')
    rating = models.IntegerField(null=True, blank=True, verbose_name='Оценка')
    comment = models.TextField(null=True, blank=True, verbose_name='Комментарий')
    created_at = models.DateTimeField(null=True, blank=True, verbose_name='Создан')
    is_moderated = models.BooleanField(default=False, verbose_name='Промодерирован')

    class Meta:
        managed = False
        db_table = 'reviews'
        verbose_name = 'Отзыв'
        verbose_name_plural = 'Отзывы'


class WithdrawalRequest(models.Model):
    id = models.BigIntegerField(primary_key=True, verbose_name='ID')
    user_id = models.BigIntegerField(null=True, blank=True, verbose_name='Пользователь')
    amount = models.FloatField(default=0, verbose_name='Сумма')
    wallet_type = models.TextField(null=True, blank=True, verbose_name='Тип кошелька')
    wallet_address = models.TextField(null=True, blank=True, verbose_name='Адрес кошелька')
    status = models.TextField(default='pending', verbose_name='Статус')
    created_at = models.DateTimeField(null=True, blank=True, verbose_name='Создана')

    class Meta:
        managed = False
        db_table = 'withdrawal_requests'
        verbose_name = 'Заявка на вывод'
        verbose_name_plural = 'Заявки на вывод'


class SupportTicket(models.Model):
    id = models.BigIntegerField(primary_key=True, verbose_name='ID')
    user_id = models.BigIntegerField(verbose_name='Пользователь')
    subject = models.TextField(null=True, blank=True, verbose_name='Тема')
    category = models.TextField(null=True, blank=True, verbose_name='Категория')
    user_type = models.TextField(default='buyer', verbose_name='Тип пользователя')
    order_number = models.TextField(null=True, blank=True, verbose_name='Номер заказа')
    status = models.TextField(default='open', verbose_name='Статус')
    assigned_to = models.TextField(null=True, blank=True, verbose_name='Назначено')
    created_at = models.DateTimeField(null=True, blank=True, verbose_name='Создан')
    updated_at = models.DateTimeField(null=True, blank=True, verbose_name='Обновлён')

    class Meta:
        managed = False
        db_table = 'support_tickets'
        verbose_name = 'Тикет'
        verbose_name_plural = 'Тикеты'


class AuthCode(models.Model):
    user_id = models.BigIntegerField(verbose_name='Пользователь')
    code = models.TextField(verbose_name='Код')
    expires_at = models.DateTimeField(verbose_name='Истекает')
    created_at = models.DateTimeField(null=True, blank=True, verbose_name='Создан')

    class Meta:
        managed = False
        db_table = 'auth_codes'
        verbose_name = 'Код авторизации'
        verbose_name_plural = 'Коды авторизации'