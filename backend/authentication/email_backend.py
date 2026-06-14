import logging
import os
from typing import Iterable

from azure.communication.email import EmailClient
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)


class AzureEmailBackend(BaseEmailBackend):
    """Django email backend backed by Azure Communication Services Email."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.connection_string = os.getenv("AZURE_EMAIL_CONNECTION_STRING") or getattr(
            settings, "AZURE_EMAIL_CONNECTION_STRING", ""
        )
        if not self.connection_string:
            raise ImproperlyConfigured("AZURE_EMAIL_CONNECTION_STRING is required for AzureEmailBackend")
        self.client = EmailClient.from_connection_string(self.connection_string)

    def send_messages(self, email_messages: Iterable):
        if not email_messages:
            return 0

        sent_count = 0
        for email_message in email_messages:
            from_email = email_message.from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "")
            if not from_email:
                err = ImproperlyConfigured("DEFAULT_FROM_EMAIL (or message.from_email) must be set")
                if self.fail_silently:
                    logger.error("AzureEmailBackend: %s", err)
                    continue
                raise err

            try:
                plain_text = email_message.body or ""
                html_content = None
                for content, mimetype in (email_message.alternatives or []):
                    if mimetype == "text/html":
                        html_content = content
                        break

                if email_message.content_subtype == "html" and email_message.body:
                    html_content = email_message.body

                message = {
                    "senderAddress": from_email,
                    "recipients": {
                        "to": [{"address": addr} for addr in (email_message.to or [])],
                        "cc": [{"address": addr} for addr in (email_message.cc or [])],
                        "bcc": [{"address": addr} for addr in (email_message.bcc or [])],
                    },
                    "content": {
                        "subject": email_message.subject or "",
                        "plainText": plain_text,
                    },
                }

                if html_content:
                    message["content"]["html"] = html_content

                poller = self.client.begin_send(message)
                poller.result()
                sent_count += 1
            except Exception:
                logger.exception("Failed to send email via AzureEmailBackend")
                if not self.fail_silently:
                    raise

        return sent_count
