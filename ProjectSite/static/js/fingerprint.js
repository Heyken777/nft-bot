// static/js/fingerprint.js — сбор fingerprint устройства (vanilla JS, SHA-256 через WebCrypto)
// Сервер не доверяет этому hash как идентификатору "настоящего" устройства —
// это лишь вспомогательный сигнал в антифрод-скоринге.
(function () {
    'use strict';

    function sha256(text) {
        if (!window.crypto || !window.crypto.subtle) return null;
        var enc = new TextEncoder().encode(text);
        return crypto.subtle.digest('SHA-256', enc).then(function (buf) {
            return Array.prototype.map.call(new Uint8Array(buf), function (b) {
                return ('00' + b.toString(16)).slice(-2);
            }).join('');
        });
    }

    function collectSignals() {
        var parts = [];
        parts.push(navigator.userAgent || '');
        parts.push(navigator.language || '');
        parts.push(Intl.DateTimeFormat().resolvedOptions().timeZone || '');
        if (navigator.hardwareConcurrency) parts.push('cores:' + navigator.hardwareConcurrency);
        parts.push('screen:' + (window.screen.width || 0) + 'x' + (window.screen.height || 0) + 'x' + (window.screen.colorDepth || 0));
        parts.push('tz:' + (new Date().getTimezoneOffset() || 0));
        parts.push('langs:' + (navigator.languages ? navigator.languages.join(',') : ''));

        // Canvas fingerprint (WebGL-подобный отпечаток рендеринга)
        try {
            var canvas = document.createElement('canvas');
            canvas.width = 240;
            canvas.height = 60;
            var ctx = canvas.getContext('2d');
            if (ctx) {
                ctx.textBaseline = 'top';
                ctx.font = '14px Arial';
                ctx.fillStyle = '#2563EB';
                ctx.fillRect(0, 0, 240, 60);
                ctx.fillStyle = '#1E293B';
                ctx.fillText('Heyken-AntiFraud', 8, 8);
                ctx.fillStyle = '#94A3B8';
                ctx.font = '11px sans-serif';
                ctx.fillText('af:' + Math.random().toString(36).slice(2, 8), 8, 30);
                parts.push('canvas:' + canvas.toDataURL());
            }
        } catch (e) { /* canvas недоступен — используем остальные сигналы */ }

        return parts.join('|');
    }

    function report(hash) {
        if (!hash) return;
        fetch('/usersite/api/fingerprint/', {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'},
            body: 'fingerprint_hash=' + encodeURIComponent(hash),
            credentials: 'same-origin'
        }).catch(function () { /* тихо игнорируем ошибки сети */ });
    }

    function run() {
        var signals = collectSignals();
        sha256(signals).then(function (hash) {
            report(hash);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', run);
    } else {
        run();
    }
})();