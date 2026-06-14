"""
WSGI config for apis project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os
from django.core.wsgi import get_wsgi_application
from .infrastructure.otel import setup_otel


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'apis.settings')

# setup_otel() must run AFTER get_wsgi_application() (which applies the LOGGING
# config) so it can wire the app loggers' export handler.
application = get_wsgi_application()
setup_otel()
