import json, os, requests, sqlite3
from datetime import datetime, timedelta
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from users.crypto_utils import decrypt_value

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, '..', 'novixgift.db')
OWNER_TELEGRAM_ID = int(os.getenv("OWNER_TELEGRAM_ID", "1803437347"))


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=40)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=40000;")
    conn.row_factory = sqlite3.Row
    return conn


def get_admin_name(request):
    return request.session.get('username') or request.session.get('admin', 'Admin')


def session_required(view_func):
    from functools import wraps
    @wraps(view_func)
    def _wrapper(request, *args, **kwargs):
        if not request.session.get('telegram_id'):
            return redirect('/')
        return view_func(request, *args, **kwargs)
    return _wrapper


def log_admin_action(request, action: str, target_id=None, amount=None):
    admin_id = request.session.get('telegram_id', 0)
    admin_name = request.session.get('username', '') or 'Admin'
    ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', '0.0.0.0')).split(',')[0].strip()
    now = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        conn = get_db()
        cur = conn.cursor()
        desc = f"CEO / Владелец {admin_name} совершил действие: {action}" if admin_id == OWNER_TELEGRAM_ID else action
        if target_id:
            desc += f" | target={target_id}"
        if amount:
            desc += f" | amount={amount}"
        desc += f" | IP: {ip}"
        cur.execute(
            "INSERT INTO audit_logs (timestamp, user_id, username, action_type, description, ip_address) VALUES (?, ?, ?, ?, ?, ?)",
            (now, admin_id, admin_name, action, desc, ip)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def log_page_view(request, action_type: str, description: str, target_id=None):
    admin_id = request.session.get('telegram_id', 0)
    admin_name = request.session.get('username', '') or 'Admin'
    ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', '0.0.0.0')).split(',')[0].strip()
    now = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        conn = get_db()
        cur = conn.cursor()
        desc = f"👑 {description} | IP: {ip}" if admin_id == OWNER_TELEGRAM_ID else f"{description} | IP: {ip}"
        if target_id:
            desc += f" | id={target_id}"
        cur.execute(
            "INSERT INTO audit_logs (timestamp, user_id, username, action_type, description, ip_address) VALUES (?, ?, ?, ?, ?, ?)",
            (now, admin_id, admin_name, action_type, desc, ip)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


ROLE_PERMISSIONS = {
    'CEO': ['*'],
    'Admin': [
        'dashboard', 'users', 'users_edit', 'deals', 'withdrawals',
        'promocodes', 'broadcast', 'disputes', 'audit',
        'tickets', 'tickets_reply', 'news', 'partnership',
    ],
    'Moderator': [
        'dashboard', 'users', 'deals', 'disputes', 'reviews',
        'tickets', 'tickets_reply', 'news',
    ],
    'Support': [
        'tickets', 'tickets_reply', 'users',
    ],
    'Analyst': [
        'dashboard', 'users', 'deals', 'audit',
    ],
}


def get_user_role(request):
    tid = request.session.get('telegram_id')
    if tid == OWNER_TELEGRAM_ID:
        return 'CEO'
    return request.session.get('admin_role', 'Admin')


def has_permission(request, permission):
    role = get_user_role(request)
    perms = ROLE_PERMISSIONS.get(role, [])
    if '*' in perms:
        return True
    return permission in perms


def require_permission(permission):
    from functools import wraps
    def decorator(view_func):
        @wraps(view_func)
        def _wrapper(request, *args, **kwargs):
            if not request.session.get('telegram_id'):
                return redirect('/')
            if not has_permission(request, permission):
                return render(request, 'no_access.html', {
                    'active_page': '',
                    'admin_name': get_admin_name(request),
                }, status=403)
            return view_func(request, *args, **kwargs)
        return _wrapper
    return decorator


PRIORITY_MAP = {
    'vip':       {'label': 'Критический', 'class': 'priority-critical', 'order': 4},
    'platinum':  {'label': 'Высокий',     'class': 'priority-high',    'order': 3},
    'premium':   {'label': 'Средний',     'class': 'priority-medium',  'order': 2},
}

def get_user_tier(user_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT premium_tier FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row else 'free'
    except Exception:
        return 'free'

def get_priority_info(user_id):
    tier = get_user_tier(user_id)
    return PRIORITY_MAP.get(tier, {'label': 'Низкий', 'class': 'priority-low', 'order': 1})


@require_permission('disputes')
def disputes_view(request):
    log_page_view(request, 'Просмотр Споров', 'Администратор открыл страницу споров и арбитража')
    disputes_data = []
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT d.*, de.seller, de.buyer, de.amount, de.currency, de.item, de.status AS deal_status
            FROM disputes d
            LEFT JOIN deals de ON d.deal_id = de.id
        """)
        rows = cur.fetchall()

        for d in rows:
            row = dict(d)
            dc = row.get('dispute_code') or f"#{row.get('id')}"
            seller_id = row.get('seller')
            buyer_id = row.get('buyer')
            cur.execute("SELECT username FROM users WHERE user_id=?", (buyer_id,))
            buyer_row = cur.fetchone()
            buyer_name = buyer_row[0] if buyer_row else str(buyer_id)
            cur.execute("SELECT username FROM users WHERE user_id=?", (seller_id,))
            seller_row = cur.fetchone()
            seller_name = seller_row[0] if seller_row else str(seller_id)
            opened_by = row.get('opened_by') or buyer_id
            priority = get_priority_info(opened_by)
            disputes_data.append({
                'id': row.get('id'),
                'dispute_code': dc,
                'username': buyer_name,
                'user_id': buyer_id,
                'buyer_id': buyer_id,
                'buyer_name': buyer_name,
                'seller_id': seller_id,
                'seller_name': seller_name,
                'subject': f"Спор {dc} по сделке #{row.get('deal_id')} — {row.get('item', 'NFT')}",
                'order_number': f"#{row.get('deal_id')}",
                'user_type': 'buyer' if row.get('initiator') == 'buyer' else 'seller',
                'status': 'open' if row.get('status') == 'pending' else row.get('status', 'closed'),
                'created_at': row.get('created_at', ''),
                'priority_label': priority['label'],
                'priority_class': priority['class'],
                'priority_order': priority['order'],
            })
        conn.close()

        disputes_data.sort(key=lambda x: (-x['priority_order'], x['created_at'] or ''), reverse=False)
        disputes_data.sort(key=lambda x: -x['priority_order'])
    except Exception as e:
        print(f"[disputes] Error: {e}")
    return render(request, 'disputes/disputes_list.html', {
        'active_page': 'disputes',
        'admin_name': get_admin_name(request),
        'disputes': disputes_data,
    })


@require_permission('disputes')
def dispute_detail_view(request, dispute_id):
    log_page_view(request, 'Просмотр Спора', f'Администратор открыл спор #{dispute_id}', target_id=dispute_id)
    dispute = None; deal = None; seller_messages = []; buyer_messages = []
    seller_info = None; buyer_info = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT d.*, de.seller, de.buyer, de.amount, de.currency, de.item, de.status AS deal_status,
                   de.created AS deal_created, de.commission
            FROM disputes d
            LEFT JOIN deals de ON d.deal_id = de.id
            WHERE d.id=?
        """, (dispute_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return render(request, 'disputes/dispute_detail.html', {
                'active_page': 'disputes',
                'admin_name': get_admin_name(request),
                'dispute': None,
            })
        dispute = dict(row)
        dispute['dispute_code'] = dispute.get('dispute_code') or f"#{dispute_id}"
        deal_id = dispute.get('deal_id')

        if deal_id:
            cur.execute("SELECT * FROM deals WHERE id=?", (deal_id,))
            deal_row = cur.fetchone()
            if deal_row:
                deal = dict(deal_row)

        seller_id = dispute.get('seller')
        buyer_id = dispute.get('buyer')

        if seller_id:
            cur.execute("SELECT * FROM users WHERE user_id=?", (seller_id,))
            srow = cur.fetchone()
            if srow:
                seller_info = dict(srow)
                seller_info['telegram_id'] = seller_info.pop('user_id')

            cur.execute(
                "SELECT id, user_id, sender_type, text, timestamp FROM user_messages WHERE user_id=? ORDER BY id ASC",
                (seller_id,)
            )
            for m in cur.fetchall():
                md = dict(m)
                md['text'] = decrypt_value(md.get('text', ''))
                seller_messages.append(md)

        if buyer_id:
            cur.execute("SELECT * FROM users WHERE user_id=?", (buyer_id,))
            brow = cur.fetchone()
            if brow:
                buyer_info = dict(brow)
                buyer_info['telegram_id'] = buyer_info.pop('user_id')

            cur.execute(
                "SELECT id, user_id, sender_type, text, timestamp FROM user_messages WHERE user_id=? ORDER BY id ASC",
                (buyer_id,)
            )
            for m in cur.fetchall():
                md = dict(m)
                md['text'] = decrypt_value(md.get('text', ''))
                buyer_messages.append(md)

        conn.close()

        opened_by = dispute.get('opened_by') or buyer_id
        priority = get_priority_info(opened_by)
        dispute['priority_label'] = priority['label']
        dispute['priority_class'] = priority['class']
        dispute['priority_order'] = priority['order']

    except Exception as e:
        print(f"[dispute_detail] Error: {e}")

    return render(request, 'disputes/dispute_detail.html', {
        'active_page': 'disputes',
        'admin_name': get_admin_name(request),
        'dispute': dispute,
        'deal': deal,
        'seller_info': seller_info,
        'buyer_info': buyer_info,
        'seller_messages': seller_messages,
        'buyer_messages': buyer_messages,
    })


@csrf_exempt
@session_required
def dispute_detail_api(request, dispute_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT d.*, de.seller, de.buyer, de.amount, de.currency, de.item, de.status AS deal_status,
                   de.created AS deal_created, de.commission
            FROM disputes d
            LEFT JOIN deals de ON d.deal_id = de.id
            WHERE d.id=?
        """, (dispute_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return JsonResponse({'error': 'Not found'}, status=404)
        data = dict(row)
        data['dispute_code'] = data.get('dispute_code') or f"#{data.get('id')}"
        return JsonResponse(data)
    except Exception as e:
        print(f"[dispute_detail] Error: {e}")
        return JsonResponse({'error': 'Server error'}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@require_permission('disputes')
def dispute_resolve_api(request, dispute_id):
    admin_id = request.session.get('telegram_id', 0)
    try:
        data = json.loads(request.body)
        decision = data.get('decision')
        reason = (data.get('reason', '') or '').strip()

        if decision not in ('seller', 'buyer'):
            return JsonResponse({'error': 'Некорректное решение'}, status=400)

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM disputes WHERE id=?", (dispute_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return JsonResponse({'error': 'Спор не найден'}, status=404)
        dispute = dict(row)
        if dispute.get('status') != 'pending':
            conn.close()
            return JsonResponse({'error': 'Спор уже закрыт'}, status=400)

        dispute_code = dispute.get('dispute_code') or f"#{dispute_id}"
        deal_id = dispute['deal_id']
        cur.execute("SELECT * FROM deals WHERE id=?", (deal_id,))
        deal_row = cur.fetchone()
        if not deal_row:
            conn.close()
            return JsonResponse({'error': 'Сделка не найдена'}, status=404)
        deal = dict(deal_row)

        currency = deal['currency']
        amount = float(deal['amount'])
        seller_id = deal['seller']
        buyer_id = deal.get('buyer')

        cur.execute("SELECT premium_tier FROM users WHERE user_id=?", (seller_id,))
        tier_row = cur.fetchone()
        tier = tier_row[0] if tier_row else 'free'
        commission_rate = {'free': 0.10, 'premium': 0.05, 'platinum': 0.03, 'vip': 0.0}.get(tier, 0.10)
        commission = amount * commission_rate
        seller_payout = round(amount - commission, 2)

        now = timezone.now().strftime('%Y-%m-%d %H:%M:%S')

        if decision == 'seller':
            cur.execute(f"UPDATE users SET balance_{currency} = COALESCE(balance_{currency},0) + ? WHERE user_id=?",
                        (seller_payout, seller_id))
            cur.execute("UPDATE deals SET status='completed', completed=? WHERE id=?", (now, deal_id))
            cur.execute(
                "UPDATE disputes SET status='seller', resolved_at=?, resolved_by=?, resolution_reason=? WHERE id=?",
                (now, admin_id, reason, dispute_id)
            )
            winner_side = 'продавца'
            action_desc = f"Спор {dispute_code}: победа продавца, выплачено {seller_payout} {currency}, причина: {reason}"
        elif decision == 'buyer':
            if not buyer_id:
                conn.close()
                return JsonResponse({'error': 'Покупатель не указан'}, status=400)
            cur.execute(f"UPDATE users SET balance_{currency} = COALESCE(balance_{currency},0) + ? WHERE user_id=?",
                        (amount, buyer_id))
            cur.execute("UPDATE deals SET status='cancelled', completed=? WHERE id=?", (now, deal_id))
            cur.execute(
                "UPDATE disputes SET status='buyer', resolved_at=?, resolved_by=?, resolution_reason=? WHERE id=?",
                (now, admin_id, reason, dispute_id)
            )
            winner_side = 'покупателя'
            action_desc = f"Спор {dispute_code}: победа покупателя, возвращено {amount} {currency}, причина: {reason}"
        else:
            conn.close()
            return JsonResponse({'error': 'Invalid decision'}, status=400)

        log_admin_action(request, action_desc, target_id=seller_id)
        conn.commit()
        conn.close()

        try:
            bot_token = os.environ.get('BOT_TOKEN', '')
            if bot_token:
                base_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                reason_text = reason or 'Решение администрации.'

                def send_tg(user_id, text):
                    requests.post(base_url, json={
                        'chat_id': user_id,
                        'text': text,
                        'parse_mode': 'Markdown'
                    }, timeout=10)

                if decision == 'seller':
                    send_tg(seller_id,
                        f"✅ *Спор {dispute_code} по сделке #{deal_id} успешно закрыт.*\n\n"
                        f"Мнение всех администраторов пало на правоту продавца.\n"
                        f"Вам зачислена сумма: {seller_payout} {currency}\n"
                        f"Официальная причина: {reason_text}")
                    if buyer_id:
                        send_tg(buyer_id,
                            f"⚖️ *Спор {dispute_code} закрыт.*\n\n"
                            f"Решением администрации победа присуждена Продавцу.\n"
                            f"Официальная причина: {reason_text}")
                else:
                    if buyer_id:
                        send_tg(buyer_id,
                            f"↩️ *Спор {dispute_code} по сделке #{deal_id} успешно закрыт.*\n\n"
                            f"Мнение всех администраторов пало на правоту покупателя.\n"
                            f"Вам зачислена сумма: {amount} {currency}\n"
                            f"Официальная причина: {reason_text}")
                    send_tg(seller_id,
                        f"⚖️ *Спор {dispute_code} закрыт.*\n\n"
                        f"Решением администрации победа присуждена Покупателю.\n"
                        f"Официальная причина: {reason_text}")
        except Exception as tg_err:
            print(f"[dispute_resolve] TG notify error: {tg_err}")

        return JsonResponse({
            'success': True,
            'dispute_code': dispute_code,
            'decision': decision,
            'winner_side': winner_side,
            'amount': amount if decision == 'buyer' else seller_payout,
            'currency': currency,
            'reason': reason
        })
    except Exception as e:
        print(f"[dispute_resolve] Error: {e}")
        return JsonResponse({'error': 'Внутренняя ошибка сервера'}, status=500)
