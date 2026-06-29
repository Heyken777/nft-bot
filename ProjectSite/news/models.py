# news/models.py
from django.db import models
from django.utils import timezone

class News(models.Model):
    title = models.CharField(max_length=200, verbose_name='Заголовок')
    short_description = models.TextField(max_length=500, verbose_name='Краткое описание')
    description = models.TextField(verbose_name='Описание', blank=True)
    content = models.TextField(verbose_name='Основной текст')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')
    is_published = models.BooleanField(default=True, verbose_name='Опубликовано')
    
    class Meta:
        verbose_name = 'Новость'
        verbose_name_plural = 'Новости'
        ordering = ['-created_at']
    
    def __str__(self):
        return self.title


class Partnership(models.Model):
    """Модель заявки на сотрудничество"""
    
    STATUS_CHOICES = [
        ('pending', 'Ожидает ответа'),
        ('approved', 'Одобрено'),
        ('rejected', 'Отказано'),
    ]
    
    TYPE_CHOICES = [
        ('advertising', 'Реклама'),
        ('blogger', 'Блогер'),
        ('partnership', 'Партнёрство'),
        ('other', 'Другое'),
    ]
    
    user_id = models.BigIntegerField(verbose_name='Telegram ID пользователя')
    full_name = models.CharField(max_length=200, verbose_name='ФИО')
    email = models.EmailField(verbose_name='Email')
    telegram = models.CharField(max_length=100, verbose_name='Telegram @username')
    partnership_type = models.CharField(max_length=50, choices=TYPE_CHOICES, default='other', verbose_name='Тип сотрудничества')
    description = models.TextField(verbose_name='Описание предложения')
    social_links = models.TextField(verbose_name='Ссылки на соцсети', blank=True, help_text='Введите ссылки через запятую')
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='pending', verbose_name='Статус')
    assigned_to = models.CharField(max_length=100, blank=True, null=True, verbose_name='Ответственный')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')
    
    class Meta:
        verbose_name = 'Заявка на сотрудничество'
        verbose_name_plural = 'Заявки на сотрудничество'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Заявка #{self.id} — {self.full_name}"


class PartnershipMessage(models.Model):
    """Сообщения в чате заявки на сотрудничество"""
    
    partnership = models.ForeignKey(Partnership, on_delete=models.CASCADE, related_name='messages')
    sender_type = models.CharField(max_length=20, choices=[('user', 'Пользователь'), ('admin', 'Администратор')])
    sender_name = models.CharField(max_length=100)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['created_at']
    
    def __str__(self):
        return f"Сообщение #{self.id} от {self.sender_name}"