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
    admin_id = models.BigIntegerField(null=True, blank=True, verbose_name='Администратор (Telegram ID)')
    action = models.TextField(null=True, blank=True, verbose_name='Действие')
    target_id = models.BigIntegerField(null=True, blank=True, verbose_name='Цель (Telegram ID)')
    amount = models.FloatField(null=True, blank=True, verbose_name='Сумма')
    timestamp = models.DateTimeField(null=True, blank=True, verbose_name='Время')

    class Meta:
        managed = False
        db_table = 'admin_logs'
        verbose_name = 'Лог админа'
        verbose_name_plural = 'Логи админов'
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.action} @ {self.timestamp}"
