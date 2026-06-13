// animations.js - Киберпанк анимации для Novix Gift (исправленная версия)

// ========== НЕОНОВЫЕ ЭФФЕКТЫ (БЕЗ ПАРАЛЛАКСА) ==========
class NeonEffects {
    constructor() {
        this.init();
    }

    init() {
        this.addButtonEffects();
        this.addCardEffects();
        this.addInputEffects();
        this.addNavEffects();
        this.addParticleBackground();
        // Глитч-эффект УБРАН - больше не будет "взрывать" текст
    }

    // Эффект для кнопок (только свечение, без изменения положения)
    addButtonEffects() {
        document.querySelectorAll('button, .btn, .plan-card, .action-card, .currency-option, .filter-btn').forEach(btn => {
            btn.addEventListener('mouseenter', (e) => {
                this.neonPulse(btn, '#00f2fe');
                this.createRipple(e, btn);
            });
            
            btn.addEventListener('mouseleave', () => {
                this.removeNeonPulse(btn);
            });
            
            btn.addEventListener('click', (e) => {
                this.flashEffect(btn);
            });
        });
    }

    // Эффект для карточек - ТОЛЬКО СВЕЧЕНИЕ, БЕЗ ДВИЖЕНИЯ
    addCardEffects() {
        document.querySelectorAll('.glass, .balance-card, .deal-card, .premium-card').forEach(card => {
            card.addEventListener('mouseenter', () => {
                card.style.transition = 'all 0.3s ease';
                card.style.boxShadow = '0 0 20px rgba(0, 242, 254, 0.3)';
                card.style.borderColor = '#00f2fe';
            });
            
            card.addEventListener('mouseleave', () => {
                card.style.boxShadow = '';
                card.style.borderColor = '';
            });
        });
    }

    // Эффект для полей ввода
    addInputEffects() {
        document.querySelectorAll('input, textarea').forEach(input => {
            input.addEventListener('focus', () => {
                this.neonBorder(input, '#00f2fe');
            });
            
            input.addEventListener('blur', () => {
                this.removeNeonBorder(input);
            });
        });
    }

    // Эффект для навигации (только подсветка)
    addNavEffects() {
        document.querySelectorAll('.nav-item').forEach(nav => {
            nav.addEventListener('mouseenter', () => {
                const icon = nav.querySelector('svg');
                const text = nav.querySelector('span');
                if (icon) icon.style.filter = 'drop-shadow(0 0 5px #00f2fe)';
                if (text) text.style.color = '#00f2fe';
            });
            
            nav.addEventListener('mouseleave', () => {
                const icon = nav.querySelector('svg');
                const text = nav.querySelector('span');
                if (icon && !nav.classList.contains('active')) icon.style.filter = 'none';
                if (text && !nav.classList.contains('active')) text.style.color = '';
            });
        });
    }

    // Неоновый пульс (без изменения положения)
    neonPulse(element, color) {
        element.style.transition = 'all 0.2s ease';
        element.style.boxShadow = `0 0 15px ${color}, 0 0 25px ${color}66`;
        if (element.classList.contains('glass')) {
            element.style.borderColor = color;
        }
    }

    removeNeonPulse(element) {
        element.style.boxShadow = '';
        if (element.classList.contains('glass')) {
            element.style.borderColor = '';
        }
    }

    // Эффект ряби при наведении
    createRipple(event, element) {
        const ripple = document.createElement('div');
        ripple.className = 'ripple-effect';
        const rect = element.getBoundingClientRect();
        const x = event.clientX - rect.left;
        const y = event.clientY - rect.top;
        ripple.style.left = `${x}px`;
        ripple.style.top = `${y}px`;
        ripple.style.position = 'absolute';
        ripple.style.width = '0';
        ripple.style.height = '0';
        ripple.style.borderRadius = '50%';
        ripple.style.background = 'radial-gradient(circle, rgba(0,242,254,0.3) 0%, rgba(0,242,254,0) 70%)';
        ripple.style.transform = 'translate(-50%, -50%)';
        ripple.style.transition = 'all 0.4s ease-out';
        ripple.style.pointerEvents = 'none';
        element.style.position = 'relative';
        element.style.overflow = 'hidden';
        element.appendChild(ripple);
        
        setTimeout(() => {
            ripple.style.width = '200px';
            ripple.style.height = '200px';
            ripple.style.opacity = '0';
        }, 10);
        
        setTimeout(() => ripple.remove(), 400);
    }

    // Эффект вспышки при клике (быстрая)
    flashEffect(element) {
        const originalBg = element.style.background;
        element.style.transition = 'background 0.05s ease';
        element.style.background = 'linear-gradient(135deg, #00f2fe, #4facfe)';
        setTimeout(() => {
            element.style.background = originalBg;
        }, 80);
    }

    // Неоновая рамка для инпутов
    neonBorder(input, color) {
        input.style.boxShadow = `0 0 8px ${color}, 0 0 15px ${color}66`;
        input.style.borderColor = color;
        input.style.transition = 'all 0.2s ease';
    }

    removeNeonBorder(input) {
        input.style.boxShadow = '';
        input.style.borderColor = '';
    }

    // Частицы фона (лёгкие, не отвлекают)
    addParticleBackground() {
        const canvas = document.createElement('canvas');
        canvas.style.position = 'fixed';
        canvas.style.top = '0';
        canvas.style.left = '0';
        canvas.style.width = '100%';
        canvas.style.height = '100%';
        canvas.style.pointerEvents = 'none';
        canvas.style.zIndex = '-1';
        canvas.style.opacity = '0.15';
        document.body.appendChild(canvas);
        
        const ctx = canvas.getContext('2d');
        let particles = [];
        
        function resizeCanvas() {
            canvas.width = window.innerWidth;
            canvas.height = window.innerHeight;
        }
        
        function createParticles() {
            particles = [];
            for (let i = 0; i < 30; i++) {
                particles.push({
                    x: Math.random() * canvas.width,
                    y: Math.random() * canvas.height,
                    radius: Math.random() * 1.5,
                    speedX: (Math.random() - 0.5) * 0.3,
                    speedY: (Math.random() - 0.5) * 0.3,
                    color: `rgba(0, 242, 254, ${Math.random() * 0.4 + 0.1})`
                });
            }
        }
        
        function drawParticles() {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            particles.forEach(p => {
                ctx.beginPath();
                ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
                ctx.fillStyle = p.color;
                ctx.fill();
                
                p.x += p.speedX;
                p.y += p.speedY;
                
                if (p.x < 0) p.x = canvas.width;
                if (p.x > canvas.width) p.x = 0;
                if (p.y < 0) p.y = canvas.height;
                if (p.y > canvas.height) p.y = 0;
            });
            requestAnimationFrame(drawParticles);
        }
        
        window.addEventListener('resize', () => {
            resizeCanvas();
            createParticles();
        });
        
        resizeCanvas();
        createParticles();
        drawParticles();
    }
}

// ========== ЗАГРУЗКА ==========
document.addEventListener('DOMContentLoaded', () => {
    // Плавное появление контента (без задержек на каждом элементе)
    const mainContainer = document.querySelector('.max-w-md') || document.body;
    mainContainer.style.opacity = '0';
    mainContainer.style.transition = 'opacity 0.4s ease';
    setTimeout(() => {
        mainContainer.style.opacity = '1';
    }, 50);
    
    // Запускаем неоновые эффекты
    new NeonEffects();
});

// ========== CSS АНИМАЦИИ ==========
const style = document.createElement('style');
style.textContent = `
    @keyframes pulseGlow {
        0%, 100% { text-shadow: 0 0 5px #00f2fe, 0 0 10px #00f2fe; }
        50% { text-shadow: 0 0 12px #00f2fe, 0 0 20px #00f2fe; }
    }
    
    .animate-pulse-glow {
        animation: pulseGlow 3s ease-in-out infinite;
    }
    
    .glass {
        transition: all 0.2s ease;
    }
    
    input, textarea {
        transition: all 0.2s ease;
    }
    
    .nav-item {
        transition: all 0.2s ease;
    }
    
    .nav-item:active {
        transform: scale(0.96);
    }
    
    .cyber-scan-line {
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        pointer-events: none;
        background: linear-gradient(180deg, 
            transparent 0%, 
            rgba(0, 242, 254, 0.03) 50%, 
            transparent 100%);
        animation: scanLine 10s linear infinite;
        z-index: 9998;
    }
    
    @keyframes scanLine {
        0% { transform: translateY(-100%); }
        100% { transform: translateY(100%); }
    }
    
    .loading-spinner {
        width: 36px;
        height: 36px;
        border: 3px solid rgba(0, 242, 254, 0.2);
        border-top-color: #00f2fe;
        border-radius: 50%;
        animation: spin 0.8s linear infinite;
    }
    
    @keyframes spin {
        to { transform: rotate(360deg); }
    }
    
    .toast-message {
        position: fixed;
        bottom: 90px;
        left: 50%;
        transform: translateX(-50%);
        background: rgba(0, 242, 254, 0.9);
        color: #0a0a12;
        padding: 8px 18px;
        border-radius: 30px;
        font-size: 13px;
        font-weight: 600;
        z-index: 10000;
        animation: toastFade 2s ease forwards;
    }
    
    @keyframes toastFade {
        0% { opacity: 0; transform: translateX(-50%) translateY(15px); }
        12% { opacity: 1; transform: translateX(-50%) translateY(0); }
        88% { opacity: 1; }
        100% { opacity: 0; visibility: hidden; }
    }
    
    button, .action-card, .plan-card {
        transition: all 0.15s ease;
    }
    
    button:active, .action-card:active, .plan-card:active {
        transform: scale(0.97);
    }
`;
document.head.appendChild(style);

// Сканирующая линия
const scanLine = document.createElement('div');
scanLine.className = 'cyber-scan-line';
document.body.appendChild(scanLine);

// Toast-уведомления
window.showToast = (message, type = 'success') => {
    const toast = document.createElement('div');
    toast.className = 'toast-message';
    toast.textContent = message;
    if (type === 'error') {
        toast.style.background = 'rgba(239, 68, 68, 0.9)';
        toast.style.color = 'white';
    } else if (type === 'warning') {
        toast.style.background = 'rgba(245, 158, 11, 0.9)';
    }
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 2000);
};

// Спиннер при клике на ссылки
document.querySelectorAll('a, .nav-item, [data-page], [onclick]').forEach(el => {
    el.addEventListener('click', (e) => {
        const href = el.getAttribute('href');
        if (href && href.startsWith('http')) return;
        
        const spinner = document.createElement('div');
        spinner.className = 'loading-spinner';
        spinner.style.position = 'fixed';
        spinner.style.top = '50%';
        spinner.style.left = '50%';
        spinner.style.transform = 'translate(-50%, -50%)';
        spinner.style.zIndex = '10001';
        document.body.appendChild(spinner);
        setTimeout(() => spinner.remove(), 800);
    });
});

// Переключение темы (есть кнопка — работает, нет — ничего не ломается)
const themeToggle = document.getElementById('themeToggle');
if (themeToggle) {
    themeToggle.addEventListener('click', () => {
        const isDark = document.body.classList.toggle('dark-theme');
        document.body.style.backgroundColor = isDark ? '#0a0a12' : '#f0f0f0';
        themeToggle.textContent = isDark ? '☀️' : '🌙';
    });
}

console.log('✅ Animations.js загружен (исправленная версия)');