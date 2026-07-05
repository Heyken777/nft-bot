(function () {
  'use strict';

  var toggle = document.querySelector('.theme-toggle');
  var icons = toggle ? toggle.querySelectorAll('.theme-toggle__icon') : [];
  var burger = document.querySelector('.burger');
  var navMenu = document.querySelector('.nav-menu');

  function syncIcons(theme) {
    if (icons.length === 2) {
      icons[0].classList.toggle('active', theme === 'light');
      icons[1].classList.toggle('active', theme === 'dark');
    }
  }

  syncIcons(document.documentElement.getAttribute('data-theme') || 'light');

  if (toggle) {
    toggle.addEventListener('click', function () {
      var html = document.documentElement;
      var current = html.getAttribute('data-theme') || 'light';
      var next = current === 'light' ? 'dark' : 'light';
      html.setAttribute('data-theme', next);
      localStorage.setItem('heyken-theme', next);
      syncIcons(next);
    });
  }

  if (burger && navMenu) {
    burger.addEventListener('click', function () {
      burger.classList.toggle('active');
      navMenu.classList.toggle('active');
      document.body.style.overflow = navMenu.classList.contains('active') ? 'hidden' : '';
    });

    var navLinks = navMenu.querySelectorAll('.nav-menu__link');
    for (var i = 0; i < navLinks.length; i++) {
      navLinks[i].addEventListener('click', function () {
        burger.classList.remove('active');
        navMenu.classList.remove('active');
        document.body.style.overflow = '';
      });
    }
  }

  var currentPath = window.location.pathname.split('/')[2] || 'dashboard';
  var links = document.querySelectorAll('.nav-menu__link[data-page]');
  for (var j = 0; j < links.length; j++) {
    if (links[j].dataset.page === currentPath) {
      links[j].classList.add('active');
    }
  }

  window.showToast = function (msg, type) {
    type = type || 'success';
    var toast = document.createElement('div');
    toast.className = 'toast-custom';
    toast.innerHTML = '<i class="fas fa-' + (type === 'success' ? 'check-circle' : 'exclamation-circle') + '"></i> ' + msg;
    document.body.appendChild(toast);
    setTimeout(function () {
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(40px)';
      toast.style.transition = 'all 300ms ease';
      setTimeout(function () { toast.remove(); }, 350);
    }, 3000);
  };

  var chatContainer = document.querySelector('.chat-messages');
  if (chatContainer) {
    chatContainer.scrollTop = chatContainer.scrollHeight;
    var observer = new MutationObserver(function () {
      chatContainer.scrollTop = chatContainer.scrollHeight;
    });
    observer.observe(chatContainer, { childList: true, subtree: true });
  }
})();
