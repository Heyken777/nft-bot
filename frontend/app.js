// Инициализация Telegram Web App
const tg = window.Telegram.WebApp;
tg.expand();
tg.enableClosingConfirmation();

// CHANGED: никогда не доверяем initDataUnsafe как источнику авторизации — только подписанному initData
const tgUser = tg.initDataUnsafe?.user || {};
const userId = tgUser.id || 0;
const userName = tgUser.username || tgUser.first_name || 'User';
const authToken = tg.initData || '';

function getAuthHeaders(extra = {}) {
    return {
        ...extra,
        'Authorization': `tma ${authToken}`,
        'X-Telegram-Init-Data': authToken
    };
}

async function apiFetch(url, options = {}) {
    const headers = getAuthHeaders(options.headers || {});
    const response = await fetch(url, { ...options, headers });
    if (response.status === 401 || response.status === 403) {
        tg.showPopup({ title: 'Ошибка авторизации', message: 'Сессия Mini App недействительна. Откройте приложение заново из Telegram.' });
    }
    return response;
}

// База данных в памяти (заглушка, в реальности через API)
let userData = {
    id: userId,
    username: userName,
    balance: {
        RUB: 0,
        USD: 0,
        EUR: 0,
        UAH: 0,
        KZT: 0,
        UZS: 0,
        BYN: 0,
        TON: 0,
        USDT: 0,
        STARS: 0
    },
    is_premium: false,
    premium_until: null,
    rating: 0,
    card: null,
    ton: null,
    reg_date: new Date().toISOString()
};

// Инициализация
document.addEventListener('DOMContentLoaded', () => {
    // Установка темы Telegram
    document.body.style.backgroundColor = tg.themeParams.bg_color;
    document.body.style.color = tg.themeParams.text_color;
    
    // Загрузка данных пользователя
    loadUserData();
    
    // Обработка текущей страницы
    initCurrentPage();
});

// Загрузка данных пользователя через API бота
async function loadUserData() {
    try {
        const response = await apiFetch(`/api/user?user_id=${userId}`);
        if (response.ok) {
            const data = await response.json();
            userData = { ...userData, ...data };
        }
    } catch (error) {
        console.error('Ошибка загрузки данных:', error);
    }
    
    updateUI();
}

// Обновление UI в зависимости от страницы
function initCurrentPage() {
    const path = window.location.pathname;
    
    if (path.includes('profile.html')) {
        initProfilePage();
    } else if (path.includes('create_deal.html')) {
        initCreateDealPage();
    } else if (path.includes('premium.html')) {
        initPremiumPage();
    } else if (path.includes('referral.html')) {
        initReferralPage();
    } else if (path.includes('deals.html')) {
        initDealsPage();
    } else {
        initHomePage();
    }
}

// Главная страница
function initHomePage() {
    // Установка имени пользователя
    const userNameEl = document.getElementById('userName');
    if (userNameEl) userNameEl.textContent = `@${userName}`;
    
    // Общий баланс
    const totalBalance = Object.values(userData.balance).reduce((a, b) => a + b, 0);
    const totalBalanceEl = document.getElementById('totalBalance');
    if (totalBalanceEl) totalBalanceEl.textContent = `${totalBalance.toFixed(2)} RUB`;
    
    // Балансы по валютам
    const currenciesGrid = document.getElementById('currenciesGrid');
    if (currenciesGrid) {
        currenciesGrid.innerHTML = '';
        for (const [currency, amount] of Object.entries(userData.balance)) {
            if (amount > 0 || currency === 'RUB') {
                const currencyDiv = document.createElement('div');
                currencyDiv.className = 'currency-card';
                currencyDiv.innerHTML = `
                    <span class="currency-symbol">${getCurrencySymbol(currency)}</span>
                    <span class="currency-amount">${amount.toFixed(2)} ${currency}</span>
                `;
                currenciesGrid.appendChild(currencyDiv);
            }
        }
    }
    
    // Обработка кнопок
    const depositBtn = document.getElementById('depositBtn');
    if (depositBtn) depositBtn.onclick = () => tg.openTelegramLink('https://t.me/NovixGift_Bot?start=deposit');
    
    const withdrawBtn = document.getElementById('withdrawBtn');
    if (withdrawBtn) withdrawBtn.onclick = () => tg.openTelegramLink('https://t.me/NovixGift_Bot?start=withdraw');
    
    // Обработка карточек действий
    document.querySelectorAll('.action-card').forEach(card => {
        card.onclick = () => {
            const page = card.dataset.page;
            if (page) navigateTo(page);
        };
    });
    
    // Обработка нижней навигации
    document.querySelectorAll('.nav-item').forEach(item => {
        item.onclick = () => {
            const page = item.dataset.page;
            if (page) navigateTo(page);
        };
    });
}

// Страница профиля
function initProfilePage() {
    // Основная информация
    document.getElementById('profileUsername').textContent = `@${userName}`;
    document.getElementById('profileId').textContent = `ID: ${userId}`;
    document.getElementById('infoUserId').textContent = userId;
    document.getElementById('infoUsername').textContent = `@${userName}`;
    document.getElementById('infoRegDate').textContent = new Date(userData.reg_date).toLocaleDateString('ru-RU');
    document.getElementById('infoCard').textContent = userData.card || 'не указаны';
    document.getElementById('infoTon').textContent = userData.ton || 'не указан';
    
    // Статистика
    document.getElementById('statDeals').textContent = userData.total_deals || 0;
    document.getElementById('statCompleted').textContent = userData.completed_deals || 0;
    document.getElementById('statRating').textContent = userData.rating || 0;
    
    // Premium статус
    const premiumStatus = document.getElementById('premiumStatus');
    if (userData.is_premium) {
        premiumStatus.textContent = userData.premium_until ? `Активен до ${new Date(userData.premium_until).toLocaleDateString('ru-RU')}` : 'Активен (FOREVER)';
        premiumStatus.style.color = '#ffd700';
    } else {
        premiumStatus.textContent = 'Не активен';
    }
    
    // Кнопка редактирования
    document.getElementById('editDetailsBtn').onclick = () => {
        tg.showPopup({
            title: 'Редактирование',
            message: 'Укажите данные для вывода средств:',
            buttons: [
                {id: 'card', type: 'default', text: '💳 Карта'},
                {id: 'ton', type: 'default', text: '💎 TON'},
                {id: 'cancel', type: 'cancel', text: 'Отмена'}
            ]
        }, (buttonId) => {
            if (buttonId === 'card') {
                tg.showPopup({
                    title: 'Введите номер карты',
                    message: 'Формат: 16 цифр',
                    buttons: [{type: 'default', text: 'ОК'}]
                });
            } else if (buttonId === 'ton') {
                tg.showPopup({
                    title: 'Введите TON кошелек',
                    message: 'Адрес начинается с UQ или EQ',
                    buttons: [{type: 'default', text: 'ОК'}]
                });
            }
        });
    };
    
    document.getElementById('upgradePremiumBtn').onclick = () => navigateTo('premium');
}

// Страница создания сделки
function initCreateDealPage() {
    let selectedCurrency = 'RUB';
    let itemName = '';
    let itemPrice = 0;
    
    // Загрузка валют
    const currenciesSelect = document.getElementById('currenciesSelect');
    const currencies = ['RUB', 'USD', 'EUR', 'UAH', 'KZT', 'UZS', 'BYN', 'TON', 'USDT', 'STARS'];
    
    currenciesSelect.innerHTML = currencies.map(curr => `
        <div class="currency-option ${curr === selectedCurrency ? 'selected' : ''}" data-currency="${curr}">
            ${getCurrencySymbol(curr)} ${curr}
        </div>
    `).join('');
    
    document.querySelectorAll('.currency-option').forEach(opt => {
        opt.onclick = () => {
            document.querySelectorAll('.currency-option').forEach(o => o.classList.remove('selected'));
            opt.classList.add('selected');
            selectedCurrency = opt.dataset.currency;
            document.getElementById('selectedCurrency').textContent = selectedCurrency;
        };
    });
    
    // Шаг 1 -> Шаг 2
    document.getElementById('nextStep1').onclick = () => {
        document.getElementById('stepCurrency').style.display = 'none';
        document.getElementById('stepName').style.display = 'block';
    };
    
    // Шаг 2 -> Шаг 3
    document.getElementById('nextStep2').onclick = () => {
        itemName = document.getElementById('itemName').value.trim();
        if (!itemName) {
            tg.showPopup({title: 'Ошибка', message: 'Введите название товара'});
            return;
        }
        document.getElementById('stepName').style.display = 'none';
        document.getElementById('stepPrice').style.display = 'block';
    };
    
    // Создание сделки
    document.getElementById('createDealBtn').onclick = async () => {
        itemPrice = parseFloat(document.getElementById('itemPrice').value);
        if (!itemPrice || itemPrice <= 0) {
            tg.showPopup({title: 'Ошибка', message: 'Введите корректную цену'});
            return;
        }
        
        tg.showPopup({
            title: 'Подтверждение',
            message: `Создать сделку?\n📦 ${itemName}\n💰 ${itemPrice} ${selectedCurrency}`,
            buttons: [
                {id: 'ok', type: 'default', text: '✅ Да'},
                {id: 'cancel', type: 'cancel', text: '❌ Нет'}
            ]
        }, async (buttonId) => {
            if (buttonId === 'ok') {
                // Отправка запроса на создание сделки
                const response = await apiFetch('/api/create_deal', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        user_id: userId,
                        item_name: itemName,
                        price: itemPrice,
                        currency: selectedCurrency
                    })
                });
                
                if (response.ok) {
                    const deal = await response.json();
                    showDealResult(deal);
                } else {
                    tg.showPopup({title: 'Ошибка', message: 'Не удалось создать сделку'});
                }
            }
        });
    };
}

// Отображение результата создания сделки
function showDealResult(deal) {
    document.querySelector('.form-container').style.display = 'none';
    const resultContainer = document.getElementById('resultContainer');
    resultContainer.style.display = 'block';
    
    document.getElementById('dealId').textContent = deal.id;
    document.getElementById('dealItem').textContent = deal.item;
    document.getElementById('dealPrice').textContent = `${deal.amount} ${deal.currency}`;
    document.getElementById('dealCommission').textContent = `${deal.commission * 100}%`;
    document.getElementById('dealLink').textContent = deal.link;
    
    document.getElementById('copyLinkBtn').onclick = () => {
        navigator.clipboard.writeText(deal.link);
        tg.showPopup({title: 'Скопировано', message: 'Ссылка скопирована!'});
    };
    
    document.getElementById('shareDealBtn').onclick = () => {
        tg.openTelegramLink(deal.link);
    };
    
    document.getElementById('newDealBtn').onclick = () => {
        location.reload();
    };
}

// Страница Premium
function initPremiumPage() {
    let selectedDays = 30;
    let selectedCurrency = 'RUB';
    const rates = {RUB: 1, USD: 92.5, EUR: 100, TON: 500, USDT: 92, STARS: 15};
    const prices = {30: 299, 45: 419, 60: 559, 90: 799, 365: 2999};
    
    // Отображение текущего статуса
    const premiumStatusText = document.getElementById('premiumStatusText');
    if (userData.is_premium) {
        premiumStatusText.innerHTML = `⭐ Premium активен${userData.premium_until ? ` до ${new Date(userData.premium_until).toLocaleDateString('ru-RU')}` : ' (FOREVER)'}`;
        premiumStatusText.style.color = '#ffd700';
    } else {
        premiumStatusText.textContent = 'Premium не активен';
    }
    
    // Выбор валюты
    const currenciesDiv = document.getElementById('premiumCurrencies');
    const currencies = ['RUB', 'USD', 'EUR', 'TON', 'USDT', 'STARS'];
    currenciesDiv.innerHTML = currencies.map(curr => `
        <div class="currency-option ${curr === selectedCurrency ? 'selected' : ''}" data-currency="${curr}">
            ${getCurrencySymbol(curr)} ${curr}
        </div>
    `).join('');
    
    document.querySelectorAll('#premiumCurrencies .currency-option').forEach(opt => {
        opt.onclick = () => {
            document.querySelectorAll('#premiumCurrencies .currency-option').forEach(o => o.classList.remove('selected'));
            opt.classList.add('selected');
            selectedCurrency = opt.dataset.currency;
            updatePrices(selectedCurrency);
        };
    });
    
    // Выбор длительности
    document.querySelectorAll('.plan-card').forEach(card => {
        card.onclick = () => {
            document.querySelectorAll('.plan-card').forEach(c => c.classList.remove('selected'));
            card.classList.add('selected');
            selectedDays = parseInt(card.dataset.days);
        };
    });
    
    // Кнопка покупки
    document.getElementById('buyPremiumBtn').onclick = () => {
        const rate = rates[selectedCurrency] || 1;
        const price = Math.floor(prices[selectedDays] / rate);
        
        tg.showPopup({
            title: 'Покупка Premium',
            message: `Подтвердите покупку:\n📅 ${selectedDays} дней\n💰 ${price} ${selectedCurrency}`,
            buttons: [
                {id: 'ok', type: 'default', text: '✅ Купить'},
                {id: 'cancel', type: 'cancel', text: '❌ Отмена'}
            ]
        }, async (buttonId) => {
            if (buttonId === 'ok') {
                tg.showPopup({title: 'Успешно!', message: 'Premium активирован!'});
            }
        });
    };
    
    function updatePrices(currency) {
        const rate = rates[currency] || 1;
        for (const [days, rubPrice] of Object.entries(prices)) {
            const convertedPrice = Math.floor(rubPrice / rate);
            const priceEl = document.getElementById(`price${days}`);
            if (priceEl) priceEl.textContent = `${convertedPrice} ${currency}`;
        }
    }
    
    updatePrices(selectedCurrency);
}

// Страница рефералов
function initReferralPage() {
    const referralLink = `https://t.me/NovixGift_Bot?start=ref_${userId}`;
    document.getElementById('referralLink').textContent = referralLink;
    document.getElementById('referralCount').textContent = userData.referral_count || 0;
    document.getElementById('referralEarnings').textContent = `${userData.referral_earnings || 0} RUB`;
    
    document.getElementById('copyRefLinkBtn').onclick = () => {
        navigator.clipboard.writeText(referralLink);
        tg.showPopup({title: 'Скопировано', message: 'Ссылка скопирована!'});
    };
    
    const referralsList = document.getElementById('referralsList');
    if (userData.referrals && userData.referrals.length > 0) {
        referralsList.innerHTML = userData.referrals.map(ref => `
            <div class="deal-card">
                <div class="deal-header">
                    <span>@${ref.username}</span>
                    <span>${new Date(ref.date).toLocaleDateString('ru-RU')}</span>
                </div>
            </div>
        `).join('');
    }
}

// Страница сделок
async function initDealsPage() {
    let currentFilter = 'all';
    
    // Загрузка сделок
    async function loadDeals() {
        const response = await apiFetch(`/api/user?user_id=${userId}`);
        if (response.ok) {
            const data = await response.json();
            renderDeals(data.deals || []);
        }
    }
    
    function renderDeals(deals) {
        const container = document.getElementById('dealsList');
        if (!deals.length) {
            container.innerHTML = '<div class="empty-state">📭 Нет сделок</div>';
            return;
        }
        
        container.innerHTML = deals.map(deal => `
            <div class="deal-card">
                <div class="deal-header">
                    <span class="deal-id">#${deal.id}</span>
                    <span class="deal-status status-${deal.status}">${getStatusText(deal.status)}</span>
                </div>
                <div class="deal-details">
                    ${deal.item} | ${deal.amount} ${deal.currency}
                </div>
                <div class="deal-actions">
                    ${deal.status === 'awaiting' && deal.role === 'buyer' ? 
                        `<button class="deal-btn pay" data-id="${deal.id}">✅ Оплатить</button>` : ''}
                    ${deal.status === 'paid' && deal.role === 'seller' ? 
                        `<button class="deal-btn confirm" data-id="${deal.id}">📦 Товар передан</button>` : ''}
                    ${deal.status === 'item_sent' && deal.role === 'buyer' ? 
                        `<button class="deal-btn confirm" data-id="${deal.id}">✅ Подтвердить</button>` : ''}
                </div>
            </div>
        `).join('');
        
        // Обработка кнопок
        document.querySelectorAll('.deal-btn.pay').forEach(btn => {
            btn.onclick = () => tg.openTelegramLink(`https://t.me/NovixGift_Bot?start=deal_${btn.dataset.id}`);
        });
    }
    
    // Фильтры
    document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.onclick = () => {
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentFilter = btn.dataset.filter;
            loadDeals();
        };
    });
    
    await loadDeals();
}

// Вспомогательные функции
function getCurrencySymbol(currency) {
    const symbols = {
        RUB: '🇷🇺',
        USD: '🇺🇸',
        EUR: '🇪🇺',
        UAH: '🇺🇦',
        KZT: '🇰🇿',
        UZS: '🇺🇿',
        BYN: '🇧🇾',
        TON: '💎',
        USDT: '💵',
        STARS: '⭐️'
    };
    return symbols[currency] || currency;
}

function getStatusText(status) {
    const statusMap = {
        'awaiting': '⏳ Ожидает оплаты',
        'paid': '💰 Оплачена',
        'item_sent': '📦 Товар передан',
        'completed': '✅ Завершена',
        'cancelled': '❌ Отменена'
    };
    return statusMap[status] || status;
}

function navigateTo(page) {
    tg.close();
    tg.openTelegramLink(`https://t.me/NovixGift_Bot?startapp=${page}`);
}