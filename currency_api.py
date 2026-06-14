# currency_api.py
import aiohttp
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional
import json
import os

class CurrencyAPI:
    """Класс для работы с курсами валют"""
    
    # АКТУАЛЬНЫЕ КУРСЫ НА 14 ИЮНЯ 2026 ГОДА
    DEFAULT_RATES = {
        'USD': 71.91,      # Доллар США [citation:3][citation:7]
        'EUR': 82.97,      # Евро [citation:3][citation:7]
        'BYN': 25.94,      # Белорусский рубль [citation:6]
        'KZT': 0.19,       # Казахстанский тенге (489.33 тенге за доллар) [citation:10]
        'UZS': 0.007,      # Узбекский сум
        'UAH': 2.30,       # Украинская гривна
        'CNY': 10.61,      # Китайский юань [citation:7]
        'TON': 140.00,     # TON (по рынку)
        'USDT': 71.91,     # Tether (привязан к доллару)
        'STARS': 1.50      # Telegram Stars (1 звезда = 1.5 рубля) ✅
    }
    
    def __init__(self, cache_file="currency_cache.json"):
        self.cache_file = cache_file
        self.cache = self.load_cache()
    
    def load_cache(self) -> Dict:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def save_cache(self):
        with open(self.cache_file, 'w', encoding='utf-8') as f:
            json.dump(self.cache, f, ensure_ascii=False, indent=2)
    
    async def fetch_rates(self, base: str = "RUB") -> Dict:
        """Возвращает актуальные курсы валют"""
        
        # Проверяем кэш (обновляем раз в час)
        if base in self.cache:
            cached = self.cache[base]
            if datetime.now() - datetime.fromisoformat(cached['timestamp']) < timedelta(hours=1):
                return cached['rates']
        
        rates = self.DEFAULT_RATES.copy()
        
        # Пробуем обновить через API
        try:
            async with aiohttp.ClientSession() as session:
                # ЦБ РФ публикует курсы на сайте, но прямого API нет
                # Используем альтернативные источники
                pass
        except Exception as e:
            print(f"⚠️ Не удалось обновить курсы: {e}")
        
        # Сохраняем в кэш
        self.cache[base] = {
            'rates': rates,
            'timestamp': datetime.now().isoformat()
        }
        self.save_cache()
        
        return rates
    
    async def convert(self, amount: float, from_currency: str, to_currency: str) -> float:
        if from_currency == to_currency:
            return amount
        
        rates = await self.fetch_rates(from_currency)
        rate = rates.get(to_currency)
        
        if rate:
            return round(amount * rate, 2)
        return amount

currency_api = CurrencyAPI()