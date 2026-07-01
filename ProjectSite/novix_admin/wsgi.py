"""
WSGI config for novix_admin project.
"""

import os, sys

from django.core.wsgi import get_wsgi_application

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'novix_admin.settings')

application = get_wsgi_application()
