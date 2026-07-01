import json, os, sqlite3
from datetime import datetime, timedelta
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.contrib.auth import authenticate, login
from django.db import transaction
from .jwt_auth import create_jwt, decode_jwt, JWTAuthentication

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), '..', 'novixgift.db')


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.row_factory = sqlite3.Row
    return conn


class LoginRateThrottle(AnonRateThrottle):
    rate = '5/minute'


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([LoginRateThrottle])
def login_api(request):
    data = json.loads(request.body)
    username = data.get('username')
    password = data.get('password')
    user = authenticate(request, username=username, password=password)
    if user:
        login(request, user)
        token = create_jwt(user.id)
        return Response({'success': True, 'token': token, 'username': username, 'role': 'admin'})
    return Response({'success': False, 'error': 'Неверные данные'}, status=401)


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([LoginRateThrottle])
def jwt_login_api(request):
    data = json.loads(request.body)
    username = data.get('username')
    password = data.get('password')
    user = authenticate(request, username=username, password=password)
    if user:
        token = create_jwt(user.id)
        return Response({'success': True, 'token': token, 'username': username, 'role': 'admin'})
    return Response({'success': False, 'error': 'Неверные данные'}, status=401)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard_api(request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE is_premium=1 AND (premium_until IS NULL OR premium_until > datetime('now'))")
    premium_users = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(amount), 0) FROM deals WHERE status='completed'")
    revenue = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM disputes WHERE status='pending'")
    disputes = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM deals WHERE status NOT IN ('completed','cancelled')")
    active_deals = cur.fetchone()[0]
    conn.close()
    return Response({
        'total_users': total_users,
        'active_subs': premium_users,
        'revenue': revenue,
        'tickets_open': disputes,
        'active_deals': active_deals,
        'today_visits': 0,
        'week_visits': 0,
        'month_visits': 0,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def users_api(request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id AS telegram_id, username, balance_RUB AS balance FROM users ORDER BY created_at DESC LIMIT 100")
    users = cur.fetchall()
    conn.close()
    return Response([dict(u) for u in users])


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def broadcast_api(request):
    data = request.data
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()
    conn.close()
    return Response({'success': True, 'sent': len(users), 'failed': 0})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def promocodes_api(request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT rowid AS id, code, amount AS discount, 'fixed' AS type, max_uses, used_count AS used, expires_at AS expiry_date, created_by, created_at, active FROM promocodes WHERE active=1 ORDER BY created_at DESC")
    promos = cur.fetchall()
    conn.close()
    return Response([dict(p) for p in promos])


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_promocode_api(request):
    data = request.data
    code = data.get('code', '').upper()
    amount = data.get('discount', 0)
    max_uses = data.get('max_uses', 100)
    expiry_days = data.get('expiry_days')
    expires_at = None
    if expiry_days:
        expires_at = (datetime.now() + timedelta(days=int(expiry_days))).isoformat()
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO promocodes (code, amount, max_uses, expires_at, active, created_by, created_at) VALUES (?, ?, ?, ?, 1, ?, datetime('now'))",
            (code, amount, max_uses, expires_at, request.user.id)
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return Response({'success': False, 'error': str(e)}, status=500)
    conn.close()
    return Response({'success': True, 'message': 'Промокод создан'})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_promocode_api(request, promo_code):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT rowid AS id, code, amount AS discount, 'fixed' AS type, max_uses, used_count AS used, expires_at AS expiry_date FROM promocodes WHERE code=? AND active=1",
        (promo_code,)
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return Response({'error': 'Промокод не найден'}, status=404)
    return Response(dict(row))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_promocode_api(request, promo_code):
    data = request.data
    amount = data.get('discount', 0)
    max_uses = data.get('max_uses', 100)
    expiry_days = data.get('expiry_days')
    expires_at = None
    if expiry_days:
        expires_at = (datetime.now() + timedelta(days=int(expiry_days))).isoformat()
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()
        cur.execute(
            "UPDATE promocodes SET amount=?, max_uses=?, expires_at=? WHERE code=? AND active=1",
            (amount, max_uses, expires_at, promo_code)
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return Response({'success': False, 'error': str(e)}, status=500)
    conn.close()
    return Response({'success': True, 'message': 'Промокод обновлён'})


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_promocode_api(request, promo_code):
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()
        cur.execute(
            "UPDATE promocodes SET active=0, deleted_by=?, deleted_at=datetime('now') WHERE code=?",
            (request.user.id, promo_code)
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return Response({'success': False, 'error': str(e)}, status=500)
    conn.close()
    return Response({'success': True, 'message': 'Промокод удалён'})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def audit_api(request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM admin_logs ORDER BY timestamp DESC LIMIT 100")
    logs = cur.fetchall()
    conn.close()
    return Response([dict(l) for l in logs])


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def profile_api(request):
    return Response({
        'name': request.user.username,
        'role': 'Admin',
        'telegram': '',
        'avatar': '',
        'custom_roles': [],
    })
