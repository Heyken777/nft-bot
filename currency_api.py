# currency_api.py
import aiohttp
import logging
from datetime import datetime, timedelta
from typing import Dict, Tuple
import json
import os

logger = logging.getLogger(__name__)

PREMIUM_RATES = {
    "RUB": 1,
    "USD": 77.5,
    "EUR": 88.1,
    "TON": 120,
    "USDT": 77.5,
    "STARS": 2,
    "UAH": 1.7,
    "KZT": 0.16,
    "UZS": 0.0065,
    "BYN": 26.5,
}

PREMIUM_PRICES_RUB = {
    30: 299,
    45: 419,
    60: 559,
    90: 799,
    365: 2999
}
class CurrencyAPI:
    def __init__(self, cache_file="currency_cache.json"):
        self.cache_file = cache_file
        self.cache = self.load_cache()
        self.last_source = "bootstrap"

    def load_cache(self) -> Dict:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Не удалось загрузить кэш валют: {e}")
                return {}
        return {}

    def save_cache(self):
        with open(self.cache_file, 'w', encoding='utf-8') as f:
            json.dump(self.cache, f, ensure_ascii=False, indent=2)

    def _default_rates(self) -> Dict:
        return {
            'RYB': 1,
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

    def _get_fresh_cache(self, base: str, max_age: timedelta = timedelta(hours=1)) -> Tuple[Dict, str] | Tuple[None, None]:
        cached = self.cache.get(base)
        if not cached:
            return None, None
        try:
            cached_at = datetime.fromisoformat(cached['timestamp'])
            if datetime.now() - cached_at < max_age:
                return cached['rates'], cached.get('source', 'cache')
        except Exception as e:
            logger.warning(f"Поврежден кэш курсов {base}: {e}")
        return None, None

    def get_stale_cache(self, base: str = "RUB") -> Dict:
        cached = self.cache.get(base)
        if not cached:
            return self._default_rates()
        return cached.get('rates', self._default_rates())

    async def fetch_rates(self, base: str = "RUB") -> Dict:
        fresh_rates, source = self._get_fresh_cache(base)
        if fresh_rates:
            self.last_source = source or "cache"
            return fresh_rates

        rates = self._default_rates()

        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get("https://api.exchangerate-api.com/v4/latest/USD") as response:
                    if response.status != 200:
                        raise RuntimeError(f"exchange API returned {response.status}")

                    data = await response.json()
                    api_rates = data.get('rates', {})
                    rub_rate = api_rates.get('RUB', 71.91)
                    for currency in list(rates.keys()):
                        if currency in api_rates and currency not in ['TON', 'USDT', 'STARS']:
                            rates[currency] = round(rub_rate / api_rates[currency], 2)

            self.cache[base] = {
                'rates': rates,
                'timestamp': datetime.now().isoformat(),
                'source': 'remote'
            }
            self.save_cache()
            self.last_source = 'remote'
            return rates
        except Exception as e:
            logger.warning(f"Не удалось обновить курсы валют: {e}")
            stale = self.get_stale_cache(base)
            self.last_source = 'stale_cache' if base in self.cache else 'fallback'
            return stale


currency_api = CurrencyAPI()
