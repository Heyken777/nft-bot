// animations.js - Киберпанк анимации для Novix Gift

// ========== НЕОНОВЫЕ ЭФФЕКТЫ ==========
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
        this.addGlitchEffect();
    }

    // Эффект для кнопок
    addButtonEffects() {
        document.querySelectorAll('button, .btn, .plan-card, .action-card, .currency-option, .filter-btn').forEach(btn => {
            // При наведении - неоновое свечение
            btn.addEventListener('mouseenter', (e) => {
                this.neonPulse(btn, '#00f2fe');
                this.createRipple(e, btn);
            });
            
            btn.addEventListener('mouseleave', () => {
                this.removeNeonPulse(btn);
            });
            
            // При клике - эффект вспышки
            btn.addEventListener('click', (e) => {
                this.flashEffect(btn);
                this.createClickWave(btn);
            });
        });
    }

    // Эффект для карточек
    addCardEffects() {
        document.querySelectorAll('.glass, .balance-card, .deal-card, .premium-card').forEach(card => {
            card.addEventListener('mousemove', (e) => {
                this.parallaxGlow(e, card);
            });
            
            card.addEventListener('mouseleave', () => {
                this.resetGlow(card);
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
            
            input.addEventListener('input', (e) => {
                this.typingEffect(input);
            });
        });
    }

    // Эффект для навигации
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

    // Неоновый пульс
    neonPulse(element, color) {
        element.style.transition = 'all 0.3s ease';
        element.style.transform = 'translateY(-2px)';
        element.style.boxShadow = `0 0 15px ${color}, 0 0 30px ${color}66`;
        if (element.classList.contains('glass')) {
            element.style.borderColor = color;
        }
    }

    removeNeonPulse(element) {
        element.style.transform = '';
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
        ripple.style.background = 'radial-gradient(circle, rgba(0,242,254,0.4) 0%, rgba(0,242,254,0) 70%)';
        ripple.style.transform = 'translate(-50%, -50%)';
        ripple.style.transition = 'all 0.5s ease-out';
        ripple.style.pointerEvents = 'none';
        element.style.position = 'relative';
        element.style.overflow = 'hidden';
        element.appendChild(ripple);
        
        setTimeout(() => {
            ripple.style.width = '300px';
            ripple.style.height = '300px';
            ripple.style.opacity = '0';
        }, 10);
        
        setTimeout(() => ripple.remove(), 500);
    }

    // Эффект вспышки при клике
    flashEffect(element) {
        const originalBg = element.style.background;
        element.style.background = 'linear-gradient(135deg, #00f2fe, #4facfe)';
        element.style.transition = 'background 0.1s ease';
        setTimeout(() => {
            element.style.background = originalBg;
        }, 100);
    }

    // Волна при клике
    createClickWave(element) {
        const wave = document.createElement('div');
        wave.className = 'click-wave';
        wave.style.position = 'fixed';
        wave.style.width = '10px';
        wave.style.height = '10px';
        wave.style.borderRadius = '50%';
        wave.style.background = 'radial-gradient(circle, #00f2fe, #4facfe)';
        wave.style.pointerEvents = 'none';
        wave.style.zIndex = '9999';
        wave.style.animation = 'waveExpand 0.6s ease-out forwards';
        document.body.appendChild(wave);
        
        setTimeout(() => wave.remove(), 600);
    }

    // Параллаксное свечение
    parallaxGlow(event, card) {
        const rect = card.getBoundingClientRect();
        const x = (event.clientX - rect.left) / rect.width;
        const y = (event.clientY - rect.top) / rect.height;
        const glowX = (x - 0.5) * 20;
        const glowY = (y - 0.5) * 20;
        card.style.transform = `perspective(1000px) rotateX(${glowY * -0.5}deg) rotateY(${glowX * 0.5}deg) translateY(-5px)`;
        card.style.transition = 'transform 0.1s ease';
        
        const gradient = `radial-gradient(circle at ${x * 100}% ${y * 100}%, rgba(0,242,254,0.15), rgba(79,172,254,0.05))`;
        card.style.background = gradient + ', rgba(10, 10, 18, 0.6)';
    }

    resetGlow(card) {
        card.style.transform = '';
        card.style.background = '';
    }

    // Неоновая рамка для инпутов
    neonBorder(input, color) {
        input.style.boxShadow = `0 0 10px ${color}, 0 0 20px ${color}66`;
        input.style.borderColor = color;
        input.style.transition = 'all 0.3s ease';
    }

    removeNeonBorder(input) {
        input.style.boxShadow = '';
        input.style.borderColor = '';
    }

    // Эффект печати
    typingEffect(input) {
        const value = input.value;
        const span = input.parentElement?.querySelector('.typing-indicator');
        if (span) {
            span.textContent = `✍️ ${value.length} символов`;
            span.style.opacity = '1';
            setTimeout(() => {
                span.style.opacity = '0';
            }, 1000);
        }
    }

    // Частицы фона
    addParticleBackground() {
        const canvas = document.createElement('canvas');
        canvas.style.position = 'fixed';
        canvas.style.top = '0';
        canvas.style.left = '0';
        canvas.style.width = '100%';
        canvas.style.height = '100%';
        canvas.style.pointerEvents = 'none';
        canvas.style.zIndex = '-1';
        canvas.style.opacity = '0.3';
        document.body.appendChild(canvas);
        
        const ctx = canvas.getContext('2d');
        let particles = [];
        
        function resizeCanvas() {
            canvas.width = window.innerWidth;
            canvas.height = window.innerHeight;
        }
        
        function createParticles() {
            particles = [];
            for (let i = 0; i < 50; i++) {
                particles.push({
                    x: Math.random() * canvas.width,
                    y: Math.random() * canvas.height,
                    radius: Math.random() * 2 + 1,
                    speedX: (Math.random() - 0.5) * 0.5,
                    speedY: (Math.random() - 0.5) * 0.5,
                    color: `rgba(0, 242, 254, ${Math.random() * 0.3 + 0.1})`
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

    // Глитч-эффект для заголовков
    addGlitchEffect() {
        const glitchElements = document.querySelectorAll('h1, h2, h3, .neon-text');
        glitchElements.forEach(el => {
            el.addEventListener('mouseenter', () => {
                const originalText = el.textContent;
                let glitchCount = 0;
                const interval = setInterval(() => {
                    if (glitchCount > 10) {
                        clearInterval(interval);
                        el.textContent = originalText;
                        return;
                    }
                    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*()';
                    let glitched = '';
                    for (let i = 0; i < originalText.length; i++) {
                        if (Math.random() > 0.7) {
                            glitched += chars[Math.floor(Math.random() * chars.length)];
                        } else {
                            glitched += originalText[i];
                        }
                    }
                    el.textContent = glitched;
                    glitchCount++;
                }, 50);
            });
        });
    }
}

// ========== ЗАГРУЗКА С АНИМАЦИЕЙ ==========
document.addEventListener('DOMContentLoaded', () => {
    // Показываем контент с fade-in
    document.querySelectorAll('.app-container, .glass, .balance-card').forEach(el => {
        el.style.opacity = '0';
        el.style.transform = 'translateY(20px)';
        el.style.transition = 'all 0.5s ease';
        setTimeout(() => {
            el.style.opacity = '1';
            el.style.transform = 'translateY(0)';
        }, 100);
    });
    
    // Запускаем неоновые эффекты
    new NeonEffects();
    
    // Добавляем анимацию для скролла
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.style.opacity = '1';
                entry.target.style.transform = 'translateY(0)';
            }
        });
    }, { threshold: 0.1 });
    
    document.querySelectorAll('.glass, .deal-card, .plan-card').forEach(el => {
        el.style.opacity = '0';
        el.style.transform = 'translateY(30px)';
        el.style.transition = 'all 0.5s ease';
        observer.observe(el);
    });
});

// ========== CSS АНИМАЦИИ ==========
const style = document.createElement('style');
style.textContent = `
    @keyframes waveExpand {
        0% {
            width: 0;
            height: 0;
            opacity: 0.8;
        }
        100% {
            width: 200px;
            height: 200px;
            opacity: 0;
        }
    }
    
    @keyframes pulseGlow {
        0%, 100% {
            text-shadow: 0 0 5px #00f2fe, 0 0 10px #00f2fe;
        }
        50% {
            text-shadow: 0 0 15px #00f2fe, 0 0 30px #00f2fe, 0 0 40px #00f2fe;
        }
    }
    
    .animate-pulse-glow {
        animation: pulseGlow 2s ease-in-out infinite;
    }
    
    .btn-hover-effect {
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }
    
    .btn-hover-effect:hover {
        transform: translateY(-3px);
        box-shadow: 0 10px 25px -5px rgba(0,242,254,0.3);
    }
    
    .ripple-effect {
        position: absolute;
        border-radius: 50%;
        transform: translate(-50%, -50%);
        pointer-events: none;
        animation: ripple 0.6s linear forwards;
    }
    
    @keyframes ripple {
        0% {
            width: 0;
            height: 0;
            opacity: 0.5;
        }
        100% {
            width: 500px;
            height: 500px;
            opacity: 0;
        }
    }
    
    .glass {
        transition: all 0.3s ease;
    }
    
    .glass:hover {
        border-color: rgba(0, 242, 254, 0.3);
    }
    
    input, textarea {
        transition: all 0.3s ease;
    }
    
    input:focus, textarea:focus {
        outline: none;
        transform: scale(1.02);
    }
    
    .nav-item {
        transition: all 0.2s ease;
    }
    
    .nav-item:active {
        transform: scale(0.95);
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
            rgba(0, 242, 254, 0.05) 50%, 
            transparent 100%);
        animation: scanLine 8s linear infinite;
        z-index: 9998;
    }
    
    @keyframes scanLine {
        0% {
            transform: translateY(-100%);
        }
        100% {
            transform: translateY(100%);
        }
    }
    
    .loading-spinner {
        width: 40px;
        height: 40px;
        border: 3px solid rgba(0, 242, 254, 0.2);
        border-top-color: #00f2fe;
        border-radius: 50%;
        animation: spin 1s linear infinite;
    }
    
    @keyframes spin {
        to { transform: rotate(360deg); }
    }
    
    .toast-message {
        position: fixed;
        bottom: 80px;
        left: 50%;
        transform: translateX(-50%);
        background: rgba(0, 242, 254, 0.9);
        color: #0a0a12;
        padding: 10px 20px;
        border-radius: 30px;
        font-size: 14px;
        font-weight: 600;
        z-index: 10000;
        animation: toastFade 2s ease forwards;
    }
    
    @keyframes toastFade {
        0% { opacity: 0; transform: translateX(-50%) translateY(20px); }
        10% { opacity: 1; transform: translateX(-50%) translateY(0); }
        90% { opacity: 1; }
        100% { opacity: 0; visibility: hidden; }
    }
`;

document.head.appendChild(style);

// Добавляем сканирующую линию
const scanLine = document.createElement('div');
scanLine.className = 'cyber-scan-line';
document.body.appendChild(scanLine);

// Функция для показа toast-уведомлений
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

// Эффект "загрузки" при переходе между страницами
document.querySelectorAll('a, .nav-item, [onclick]').forEach(el => {
    el.addEventListener('click', (e) => {
        const spinner = document.createElement('div');
        spinner.className = 'loading-spinner';
        spinner.style.position = 'fixed';
        spinner.style.top = '50%';
        spinner.style.left = '50%';
        spinner.style.transform = 'translate(-50%, -50%)';
        spinner.style.zIndex = '10001';
        document.body.appendChild(spinner);
        setTimeout(() => spinner.remove(), 1000);
    });
});

document.getElementById('themeToggle')?.addEventListener('click', () => {
    const isDark = document.body.classList.toggle('dark-theme');
    document.body.style.backgroundColor = isDark ? '#0a0a12' : '#f0f0f0';
    document.getElementById('themeToggle').textContent = isDark ? '🌞' : '🌙';
});