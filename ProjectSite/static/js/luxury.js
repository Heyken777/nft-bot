(function () {
  'use strict';

  var chatContainer = document.querySelector('.chat-messages');
  if (chatContainer) {
    chatContainer.scrollTop = chatContainer.scrollHeight;
    var observer = new MutationObserver(function () {
      chatContainer.scrollTop = chatContainer.scrollHeight;
    });
    observer.observe(chatContainer, { childList: true, subtree: true });
  }
})();
