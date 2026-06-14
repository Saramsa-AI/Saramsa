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

# get_wsgi_application() runs django.setup(), which applies the LOGGING
# dictConfig (instantiating the app loggers). setup_otel() must run AFTER that
# so its handler-routing loop can find the propagate=False app loggers and
# export their records to App Insights — otherwise only root is wired.
application = get_wsgi_application()

# Initialize OpenTelemetry
setup_otel()
