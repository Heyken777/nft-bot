// ========== АНИМАЦИИ ДЛЯ NOVIX GIFT ==========
// Современные крипто-кошельковые эффекты

class NovixAnimations {
    constructor() {
        this.init();
    }

    init() {
        this.addFadeInOnLoad();
        this.addSlideUpOnScroll();
        this.addStaggerAnimation();
        this.addRippleEffect();
        this.addGlowOnHover();
        this.addScaleOnTap();
        this.addFloatingAnimation();
        this.addNeonPulse();
        this.addSkeletonLoader();
        this.initIntersectionObserver();
        this.addParallaxGlow();
    }

    // 1. Fade In при загрузке
    addFadeInOnLoad() {
        document.querySelectorAll('.page.active .glass, .page.active .glass-light, .page.active .balance-card').forEach(el => {
            el.style.opacity = '0';
            el.style.animation = 'fadeIn 0.4s cubic-bezier(0.4, 0, 0.2, 1) forwards';
        });
    }

    // 2. Slide Up при скролле
    addSlideUpOnScroll() {
        const elements = document.querySelectorAll('.deal-card, .currency-item, .action-card');
        elements.forEach((el, index) => {
            el.style.opacity = '0';
            el.style.transform = 'translateY(20px)';
            el.style.transition = `all 0.4s cubic-bezier(0.4, 0, 0.2, 1) ${index * 0.05}s`;
        });
    }

    // 3. Stagger анимация для списков
    addStaggerAnimation() {
        const containers = ['.deals-list', '#allBalancesList', '#profileBalances'];
        containers.forEach(selector => {
            const container = document.querySelector(selector);
            if (container) {
                const items = container.children;
                Array.from(items).forEach((item, i) => {
                    item.style.opacity = '0';
                    item.style.transform = 'translateX(-10px)';
                    item.style.transition = `all 0.3s cubic-bezier(0.4, 0, 0.2, 1) ${i * 0.03}s`;
                    setTimeout(() => {
                        item.style.opacity = '1';
                        item.style.transform = 'translateX(0)';
                    }, 100);
                });
            }
        });
    }

    // 4. Ripple эффект при клике
    addRippleEffect() {
        document.querySelectorAll('button, .action-card, .nav-item, .plan-card, .currency-item').forEach(el => {
            el.addEventListener('click', (e) => {
                const rect = el.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const y = e.clientY - rect.top;
                
                const ripple = document.createElement('span');
                ripple.style.position = 'absolute';
                ripple.style.left = `${x}px`;
                ripple.style.top = `${y}px`;
                ripple.style.width = '10px';
                ripple.style.height = '10px';
                ripple.style.borderRadius = '50%';
                ripple.style.background = 'radial-gradient(circle, rgba(123, 63, 242, 0.6), rgba(123, 63, 242, 0) 70%)';
                ripple.style.transform = 'scale(0)';
                ripple.style.animation = 'ripple 0.6s ease-out';
                ripple.style.pointerEvents = 'none';
                
                el.style.position = 'relative';
                el.style.overflow = 'hidden';
                el.appendChild(ripple);
                
                setTimeout(() => ripple.remove(), 600);
            });
        });
    }

    // 5. Glow эффект при наведении
    addGlowOnHover() {
        document.querySelectorAll('.glass, .glass-light, .balance-card').forEach(el => {
            el.addEventListener('mouseenter', () => {
                el.style.transition = 'all 0.3s ease';
                el.style.boxShadow = '0 10px 30px rgba(0, 0, 0, 0.35), 0 0 30px rgba(123, 63, 242, 0.2)';
                if (el.classList.contains('glass')) {
                    el.style.borderColor = 'rgba(123, 63, 242, 0.3)';
                }
            });
            
            el.addEventListener('mouseleave', () => {
                el.style.boxShadow = '';
                if (el.classList.contains('glass')) {
                    el.style.borderColor = '';
                }
            });
        });
    }

    // 6. Scale on Tap (для мобильных)
    addScaleOnTap() {
        document.querySelectorAll('button, .action-card, .plan-card, .currency-item, .nav-item').forEach(el => {
            el.addEventListener('touchstart', () => {
                el.style.transform = 'scale(0.97)';
            });
            el.addEventListener('touchend', () => {
                el.style.transform = '';
            });
            el.addEventListener('touchcancel', () => {
                el.style.transform = '';
            });
        });
    }

    // 7. Floating анимация для элементов
    addFloatingAnimation() {
        const floatElements = document.querySelectorAll('.balance-card, .premium-card, .action-card');
        floatElements.forEach((el, index) => {
            el.style.animation = `float 4s ease-in-out ${index * 0.5}s infinite`;
        });
        
        // Добавляем стиль для floating
        if (!document.querySelector('#float-style')) {
            const style = document.createElement('style');
            style.id = 'float-style';
            style.textContent = `
                @keyframes float {
                    0%, 100% { transform: translateY(0px); }
                    50% { transform: translateY(-5px); }
                }
            `;
            document.head.appendChild(style);
        }
    }

    // 8. Neon Pulse для активных элементов
    addNeonPulse() {
        const activeElements = document.querySelectorAll('.nav-item.active, .filter-btn.active, .plan-card.selected');
        activeElements.forEach(el => {
            el.style.animation = 'neonPulse 2s ease-in-out infinite';
        });
        
        if (!document.querySelector('#neon-style')) {
            const style = document.createElement('style');
            style.id = 'neon-style';
            style.textContent = `
                @keyframes neonPulse {
                    0%, 100% { 
                        box-shadow: 0 0 5px rgba(123, 63, 242, 0.3);
                        border-color: rgba(123, 63, 242, 0.5);
                    }
                    50% { 
                        box-shadow: 0 0 20px rgba(123, 63, 242, 0.6);
                        border-color: rgba(123, 63, 242, 0.8);
                    }
                }
            `;
            document.head.appendChild(style);
        }
    }

    // 9. Skeleton Loader для загрузки
    addSkeletonLoader() {
        const loadingElements = document.querySelectorAll('.loading, [data-loading]');
        loadingElements.forEach(el => {
            el.classList.add('shimmer');
        });
    }

    // 10. Intersection Observer для анимации при скролле
    initIntersectionObserver() {
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('visible');
                    if (entry.target.classList.contains('stagger-item')) {
                        const children = entry.target.children;
                        Array.from(children).forEach((child, i) => {
                            setTimeout(() => {
                                child.style.opacity = '1';
                                child.style.transform = 'translateY(0)';
                            }, i * 50);
                        });
                    }
                }
            });
        }, { threshold: 0.1, rootMargin: '0px 0px -50px 0px' });

        document.querySelectorAll('.stagger-item, .deal-card, .currency-item').forEach(el => {
            observer.observe(el);
        });
    }

    // 11. Parallax Glow (движение за мышью)
    addParallaxGlow() {
        const cards = document.querySelectorAll('.balance-card, .glass');
        cards.forEach(card => {
            card.addEventListener('mousemove', (e) => {
                const rect = card.getBoundingClientRect();
                const x = (e.clientX - rect.left) / rect.width;
                const y = (e.clientY - rect.top) / rect.height;
                
                const glowX = (x - 0.5) * 20;
                const glowY = (y - 0.5) * 20;
                
                card.style.transform = `perspective(1000px) rotateX(${glowY * -0.3}deg) rotateY(${glowX * 0.3}deg)`;
                card.style.transition = 'transform 0.1s ease';
            });
            
            card.addEventListener('mouseleave', () => {
                card.style.transform = '';
            });
        });
    }
}

// ========== TOAST УВЕДОМЛЕНИЯ (с анимацией) ==========
window.showToast = (message, type = 'success') => {
    const toast = document.createElement('div');
    toast.className = 'toast-message';
    toast.textContent = message;
    
    if (type === 'error') {
        toast.style.background = 'rgba(239, 68, 68, 0.9)';
        toast.style.color = 'white';
    } else if (type === 'warning') {
        toast.style.background = 'rgba(245, 158, 11, 0.9)';
    } else {
        toast.style.background = 'linear-gradient(135deg, #7B3FF2, #5A46FF)';
        toast.style.color = 'white';
    }
    
    toast.style.position = 'fixed';
    toast.style.bottom = '90px';
    toast.style.left = '50%';
    toast.style.transform = 'translateX(-50%)';
    toast.style.padding = '12px 24px';
    toast.style.borderRadius = '30px';
    toast.style.fontSize = '14px';
    toast.style.fontWeight = '600';
    toast.style.zIndex = '10000';
    toast.style.backdropFilter = 'blur(10px)';
    toast.style.boxShadow = '0 10px 25px rgba(0, 0, 0, 0.2)';
    toast.style.animation = 'toastSlideUp 0.3s ease forwards';
    
    document.body.appendChild(toast);
    
    setTimeout(() => {
        toast.style.animation = 'toastSlideDown 0.3s ease forwards';
        setTimeout(() => toast.remove(), 300);
    }, 2000);
};

// Добавляем стили для toast
if (!document.querySelector('#toast-style')) {
    const style = document.createElement('style');
    style.id = 'toast-style';
    style.textContent = `
        @keyframes toastSlideUp {
            from {
                opacity: 0;
                transform: translateX(-50%) translateY(20px);
            }
            to {
                opacity: 1;
                transform: translateX(-50%) translateY(0);
            }
        }
        @keyframes toastSlideDown {
            from {
                opacity: 1;
                transform: translateX(-50%) translateY(0);
            }
            to {
                opacity: 0;
                transform: translateX(-50%) translateY(20px);
            }
        }
    `;
    document.head.appendChild(style);
}

// ========== SPINNER ДЛЯ ЗАГРУЗКИ ==========
window.showSpinner = () => {
    const spinner = document.createElement('div');
    spinner.className = 'loading-spinner';
    spinner.style.position = 'fixed';
    spinner.style.top = '50%';
    spinner.style.left = '50%';
    spinner.style.transform = 'translate(-50%, -50%)';
    spinner.style.width = '40px';
    spinner.style.height = '40px';
    spinner.style.border = '3px solid rgba(123, 63, 242, 0.2';
    spinner.style.borderTopColor = '#7B3FF2';
    spinner.style.borderRadius = '50%';
    spinner.style.zIndex = '10001';
    spinner.style.animation = 'spin 0.8s linear infinite';
    document.body.appendChild(spinner);
    return spinner;
};

window.hideSpinner = (spinner) => {
    if (spinner) spinner.remove();
};

// ========== ИНИЦИАЛИЗАЦИЯ ==========
document.addEventListener('DOMContentLoaded', () => {
    // Запускаем анимации
    new NovixAnimations();
    
    // Добавляем классы для stagger элементов
    document.querySelectorAll('.deals-list, .currency-list').forEach(el => {
        el.classList.add('stagger-item');
    });
    
    console.log('✅ Novix Gift анимации загружены');
});