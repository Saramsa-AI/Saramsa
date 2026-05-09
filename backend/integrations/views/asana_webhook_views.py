"""Asana webhook receiver — public endpoint, no auth, signature-verified.

Two roles for the same URL:

1. **Handshake**: when Asana creates a webhook, it POSTs to the target
   with an X-Hook-Secret header and an empty body. We persist the
   secret on the per-target config and echo the same header back with
   200. Must complete in well under 10 seconds; we do exactly one DB
   write and respond.

2. **Delivery**: subsequent POSTs carry an events array. We HMAC-verify
   the raw body against the stored secret, return 200, and dispatch a
   Celery task per event so the receiver itself never blocks on Asana
   API calls.
"""

import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils import timezone as dj_timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from ..models import IntegrationAccount, Project

logger = logging.getLogger(__name__)


def _find_integration_for_project(saramsa_project_id: str):
    project = Project.objects.filter(id=saramsa_project_id).first()
    if not project or not project.organization_id:
        return None, None
    integration = IntegrationAccount.objects.filter(
        organization_id=project.organization_id,
        provider="asana",
        is_active=True,
    ).first()
    return project, integration


def _persist_webhook_secret(integration: IntegrationAccount, saramsa_project_id: str, secret: str) -> None:
    config = dict(integration.config or {})
    targets = dict(config.get("asanaProjectTargets") or {})
    target = dict(targets.get(saramsa_project_id) or {})
    target["webhook_secret"] = secret
    targets[saramsa_project_id] = target
    config["asanaProjectTargets"] = targets
    integration.config = config
    integration.updated_at = dj_timezone.now()
    integration.save(update_fields=["config", "updated_at"])


@csrf_exempt
@require_POST
def asana_webhook_receiver(request, project_id: str) -> HttpResponse:
    """Receive Asana webhook events for one Saramsa project.

    Returns 200 fast on valid handshakes/deliveries, 401 on signature
    mismatch, 404 if no Asana integration is configured for the project.
    """
    project, integration = _find_integration_for_project(project_id)
    if not integration:
        return JsonResponse({"detail": "Asana integration not found"}, status=404)

    target = ((integration.config or {}).get("asanaProjectTargets") or {}).get(project_id) or {}

    handshake_secret = request.headers.get("X-Hook-Secret")
    if handshake_secret:
        _persist_webhook_secret(integration, project_id, handshake_secret)
        response = HttpResponse(status=200)
        response.headers["X-Hook-Secret"] = handshake_secret
        return response

    signature = request.headers.get("X-Hook-Signature")
    secret = target.get("webhook_secret")
    raw_body = request.body or b""

    if not signature or not secret:
        logger.warning("Asana webhook missing signature or stored secret for project=%s", project_id)
        return JsonResponse({"detail": "Signature required"}, status=401)

    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        logger.warning("Asana webhook signature mismatch for project=%s", project_id)
        return JsonResponse({"detail": "Invalid signature"}, status=401)

    try:
        body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON"}, status=400)

    events = body.get("events") or []
    from ..tasks import apply_asana_event_task

    for event in events:
        apply_asana_event_task.delay(project_id, event)

    return HttpResponse(status=200)
