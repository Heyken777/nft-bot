import jwt
from datetime import datetime, timedelta
from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed


class SimpleUser:
    def __init__(self, uid):
        self.id = uid
        self.pk = uid
        self.is_authenticated = True
        self.is_active = True

    def __str__(self):
        return str(self.id)


def get_secret():
    return settings.SECRET_KEY


def create_jwt(user_id, days=7):
    payload = {
        'user_id': str(user_id),
        'exp': datetime.utcnow() + timedelta(days=days),
        'iat': datetime.utcnow(),
    }
    return jwt.encode(payload, get_secret(), algorithm='HS256')


def decode_jwt(token):
    try:
        payload = jwt.decode(token, get_secret(), algorithms=['HS256'])
        return payload
    except jwt.ExpiredSignatureError:
        raise AuthenticationFailed('Token expired')
    except jwt.InvalidTokenError:
        raise AuthenticationFailed('Invalid token')


class JWTAuthentication(BaseAuthentication):
    def authenticate(self, request):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return None
        token = auth_header.split(' ')[1]
        payload = decode_jwt(token)
        user_id = payload.get('user_id')
        if not user_id:
            raise AuthenticationFailed('Invalid token payload')
        return (SimpleUser(user_id), None)
