from django.db import models


class Deal(models.Model):
    deal_id = models.AutoField(
        primary_key=True,
        db_column='id',
        verbose_name='ID сделки',
    )
    seller_id = models.BigIntegerField(
        db_column='seller',
        null=True, blank=True,
        verbose_name='Продавец (Telegram ID)',
    )
    buyer_id = models.BigIntegerField(
        db_column='buyer',
        null=True, blank=True,
        verbose_name='Покупатель (Telegram ID)',
    )
    asset_name = models.TextField(
        db_column='item',
        null=True, blank=True,
        verbose_name='Название NFT',
    )
    amount = models.FloatField(
        null=True, blank=True,
        verbose_name='Сумма сделки',
    )
    commission = models.FloatField(
        null=True, blank=True,
        verbose_name='Комиссия',
    )
    currency = models.TextField(
        default='RUB',
        verbose_name='Валюта',
    )
    status = models.TextField(
        default='awaiting',
        verbose_name='Статус',
        help_text="awaiting → payment_pending → paid → item_sent → completed / disputed / cancelled",
    )
    created_at = models.DateTimeField(
        db_column='created',
        auto_now_add=True,
        verbose_name='Создана',
    )
    completed_at = models.DateTimeField(
        db_column='completed',
        null=True, blank=True,
        verbose_name='Завершена',
    )
    payment_method = models.TextField(
        default='internal',
        verbose_name='Способ оплаты',
    )
    payment_comment = models.TextField(
        null=True, blank=True,
        verbose_name='Комментарий платежа',
    )
    payment_address = models.TextField(
        null=True, blank=True,
        verbose_name='Адрес для on-chain оплаты',
    )
    payment_amount = models.FloatField(
        null=True, blank=True,
        verbose_name='Сумма on-chain',
    )
    paid_tx_hash = models.TextField(
        null=True, blank=True,
        verbose_name='Hash транзакции',
    )
    paid_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Оплачено',
    )
    disputed_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Переведено в спор',
    )

    class Meta:
        managed = False
        db_table = 'deals'
        verbose_name = 'Сделка'
        verbose_name_plural = 'Сделки'
        ordering = ['-created_at']

    def __str__(self):
        return f"Deal #{self.deal_id} | {self.asset_name or '—'} | {self.amount} {self.currency} | {self.status}"
