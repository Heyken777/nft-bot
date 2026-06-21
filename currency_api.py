# currency_api.py
import aiohttp
from datetime import datetime, timedelta
from typing import Dict
import json
import os

class CurrencyAPI:
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
        # Проверяем кэш (обновляем раз в час)
        if base in self.cache:
            cached = self.cache[base]
            if datetime.now() - datetime.fromisoformat(cached['timestamp']) < timedelta(hours=1):
                return cached['rates']
        
        # Резервные курсы (сколько RUB за 1 единицу валюты)
        rates = {
            'USD': 73.0,
            'EUR': 83.0,
            'BYN': 26.0,
            'KZT': 0.15,
            'UZS': 0.0061,
            'UAH': 1.6,
            'TON': 120.0,
            'USDT': 73.0,
            'STARS': 2.0
        }
        
        # Пробуем получить курсы из API
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        api_rates = data.get('rates', {})
                        # API возвращает курсы относительно USD, конвертируем в RUB
                        rub_rate = api_rates.get('RUB', 71.91)
                        for currency in rates.keys():
                            if currency in api_rates and currency not in ['TON', 'USDT', 'STARS']:
                                rates[currency] = round(rub_rate / api_rates[currency], 2)
        except Exception as e:
            print(f"⚠️ Не удалось обновить курсы: {e}")
        
        # Сохраняем в кэш
        self.cache[base] = {
            'rates': rates,
            'timestamp': datetime.now().isoformat()
        }
        self.save_cache()
        
        return rates

currency_api = CurrencyAPI()