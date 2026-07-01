import jwt
import os
from datetime import datetime, timedelta
from django.conf import settings
from django.contrib.auth.models import User
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed


def get_secret():
    return settings.SECRET_KEY


def create_jwt(user_id, days=7):
    payload = {
        'user_id': user_id,
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
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            raise AuthenticationFailed('User not found')
        return (user, None)
