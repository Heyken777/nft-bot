/* ============================================
   PREMIUM USERSITE - QUIET LUXURY UI
   ES6+, Smooth Scroll, Fade-in Animations
   ============================================ */

   (function() {
    'use strict';
    
    // ========== DOM Elements ==========
    const header = document.querySelector('.header');
    const burger = document.querySelector('.burger');
    const navMenu = document.querySelector('.nav-menu');
    const themeToggle = document.querySelector('.theme-toggle');
    const fadeElements = document.querySelectorAll('.fade-up');
    
    // ========== Theme Management ==========
    const THEME_KEY = 'luxury-theme';
    
    function setTheme(theme) {
        if (theme === 'light') {
            document.body.classList.add('theme-light');
            localStorage.setItem(THEME_KEY, 'light');
        } else {
            document.body.classList.remove('theme-light');
            localStorage.setItem(THEME_KEY, 'dark');
        }
    }
    
    function initTheme() {
        const savedTheme = localStorage.getItem(THEME_KEY);
        if (savedTheme === 'light') {
            setTheme('light');
        }
    }
    
    function toggleTheme() {
        const isLight = document.body.classList.contains('theme-light');
        setTheme(isLight ? 'dark' : 'light');
    }
    
    // ========== Sticky Header with Intersection Observer ==========
    const headerObserver = new IntersectionObserver(
        (entries) => {
            entries.forEach(entry => {
                if (!entry.isIntersecting) {
                    header.classList.add('scrolled');
                } else {
                    header.classList.remove('scrolled');
                }
            });
        },
        { threshold: 0.1 }
    );
    
    const heroSection = document.querySelector('.hero');
    if (heroSection) {
        headerObserver.observe(heroSection);
    }
    
    // ========== Mobile Menu ==========
    function toggleMobileMenu() {
        burger.classList.toggle('active');
        navMenu.classList.toggle('active');
        document.body.style.overflow = navMenu.classList.contains('active') ? 'hidden' : '';
    }
    
    function closeMobileMenu() {
        burger.classList.remove('active');
        navMenu.classList.remove('active');
        document.body.style.overflow = '';
    }
    
    if (burger) {
        burger.addEventListener('click', toggleMobileMenu);
    }
    
    // Закрытие меню при клике на ссылку
    document.querySelectorAll('.nav-menu__link').forEach(link => {
        link.addEventListener('click', closeMobileMenu);
    });
    
    // ========== Smooth Scroll ==========
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function(e) {
            e.preventDefault();
            const target = document.querySelector(this.getAttribute('href'));
            if (target) {
                target.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            }
        });
    });
    
    // ========== Fade-in Animation on Scroll (Intersection Observer) ==========
    const fadeObserver = new IntersectionObserver(
        (entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('visible');
                    fadeObserver.unobserve(entry.target);
                }
            });
        },
        { threshold: 0.1, rootMargin: '0px 0px -50px 0px' }
    );
    
    fadeElements.forEach(el => fadeObserver.observe(el));
    
    // ========== Telegram Login Widget Handler ==========
    window.onTelegramAuth = function(user) {
        // Отправка данных на сервер для аутентификации
        fetch('/usersite/telegram-auth/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: JSON.stringify({
                id: user.id,
                username: user.username,
                first_name: user.first_name,
                last_name: user.last_name,
                photo_url: user.photo_url,
                auth_date: user.auth_date,
                hash: user.hash
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                window.location.href = '/usersite/dashboard/';
            } else {
                showToast('Ошибка входа. Попробуйте снова.', 'error');
            }
        })
        .catch(() => showToast('Ошибка соединения', 'error'));
    };
    
    // ========== Helper: Get CSRF Token ==========
    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }
    
    // ========== Toast Notifications ==========
    function showToast(message, type = 'success') {
        const toast = document.createElement('div');
        toast.className = 'toast';
        toast.textContent = message;
        toast.style.cssText = `
            position: fixed;
            bottom: 32px;
            right: 32px;
            background: ${type === 'success' ? 'var(--accent)' : 'var(--luxury-bronze)'};
            color: var(--luxury-ebony);
            padding: 12px 24px;
            border-radius: 0;
            z-index: 1100;
            animation: slideIn 0.3s ease;
            cursor: pointer;
        `;
        document.body.appendChild(toast);
        setTimeout(() => {
            toast.style.animation = 'slideOut 0.3s ease';
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }
    
    // ========== Chat Auto-scroll ==========
    const chatContainer = document.querySelector('.chat-messages');
    if (chatContainer) {
        chatContainer.scrollTop = chatContainer.scrollHeight;
        
        const chatObserver = new MutationObserver(() => {
            chatContainer.scrollTop = chatContainer.scrollHeight;
        });
        
        chatObserver.observe(chatContainer, { childList: true, subtree: true });
    }
    
    // ========== Theme Toggle ==========
    if (themeToggle) {
        themeToggle.addEventListener('click', toggleTheme);
    }
    
    // ========== Initialize ==========
    initTheme();
    
    // Добавляем стили для анимаций
    const style = document.createElement('style');
    style.textContent = `
        @keyframes slideIn {
            from { transform: translateX(100px); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        @keyframes slideOut {
            from { transform: translateX(0); opacity: 1; }
            to { transform: translateX(100px); opacity: 0; }
        }
    `;
    document.head.appendChild(style);
    
    console.log('✨ Quiet Luxury UI initialized');
})();