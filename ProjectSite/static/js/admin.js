// static/admin.js
function showToast(message, type = 'success') {
    const toast = document.createElement('div');
    toast.className = 'toast-custom';
    toast.innerHTML = `<i class="fas fa-${type === 'success' ? 'check-circle' : 'exclamation-circle'} me-2"></i> ${message}`;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}

function formatDate(dateString) {
    if (!dateString) return '—';
    return new Date(dateString).toLocaleString('ru-RU');
}