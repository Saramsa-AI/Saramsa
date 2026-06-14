import json
import logging

from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apis.core.error_handlers import handle_service_errors
from apis.core.response import StandardResponse
from authentication.authentication import AppJWTAuthentication

from .models import BillingWebhookEvent
from .quota import _get_or_create_record, _get_limits
from .services import StripeBillingService

logger = logging.getLogger(__name__)


class StripeCheckoutSessionView(APIView):
    authentication_classes = [AppJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_service_errors
    def post(self, request):
        service = StripeBillingService()
        data = service.create_checkout_session(
            user_id=str(request.user.id),
            price_id=request.data.get("price_id"),
            success_url=request.data.get("success_url"),
            cancel_url=request.data.get("cancel_url"),
        )
        return StandardResponse.success(data=data, message="Checkout session created.")


class StripeBillingPortalSessionView(APIView):
    authentication_classes = [AppJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_service_errors
    def post(self, request):
        service = StripeBillingService()
        data = service.create_billing_portal_session(
            user_id=str(request.user.id),
            return_url=request.data.get("return_url"),
        )
        return StandardResponse.success(data=data, message="Billing portal session created.")


class StripeSubscriptionStatusView(APIView):
    authentication_classes = [AppJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_service_errors
    def get(self, request):
        service = StripeBillingService()
        data = service.get_subscription_status(user_id=str(request.user.id))
        return StandardResponse.success(data=data, message="Subscription status fetched.")


class StripeCancelSubscriptionView(APIView):
    authentication_classes = [AppJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_service_errors
    def post(self, request):
        service = StripeBillingService()
        data = service.cancel_subscription(user_id=str(request.user.id))
        return StandardResponse.success(data=data, message="Subscription will cancel at the end of the billing period.")


class StripeResumeSubscriptionView(APIView):
    authentication_classes = [AppJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_service_errors
    def post(self, request):
        service = StripeBillingService()
        data = service.resume_subscription(user_id=str(request.user.id))
        return StandardResponse.success(data=data, message="Subscription resumed successfully.")


class UsageView(APIView):
    """Return current user's monthly usage and limits."""
    authentication_classes = [AppJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_service_errors
    def get(self, request):
        user_id = str(request.user.id)
        record = _get_or_create_record(user_id)
        limits = _get_limits(user_id)

        data = {
            "period": record.period,
            "usage": {
                "analysis_count": record.analysis_count,
                "work_item_gen_count": record.work_item_gen_count,
                "llm_tokens_used": record.llm_tokens_used,
            },
            "limits": limits,
        }
        return StandardResponse.success(data=data, message="Usage fetched.")


@csrf_exempt
def stripe_webhook(request):
    if request.method != "POST":
        return HttpResponse(status=405)

    payload = request.body
    signature = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    service = StripeBillingService()

    try:
        event = service.verify_webhook_event(payload, signature)
    except Exception as e:
        logger.warning("Invalid Stripe webhook signature/payload: %s", e)
        return HttpResponse(status=400)

    event_id = str(event.get("id") or "")
    if not event_id:
        return HttpResponse(status=400)

    event_type = str(event.get("type") or "")
    event_payload = event if isinstance(event, dict) else json.loads(json.dumps(event))

    webhook_event, created = BillingWebhookEvent.objects.get_or_create(
        stripe_event_id=event_id,
        defaults={
            "event_type": event_type,
            "payload": event_payload,
            "livemode": bool(event.get("livemode")),
            "processed": False,
        },
    )

    if not created and webhook_event.processed:
        return HttpResponse(status=200)

    try:
        service.process_event(event)
        webhook_event.event_type = event_type
        webhook_event.payload = event_payload
        webhook_event.livemode = bool(event.get("livemode"))
        webhook_event.processed = True
        webhook_event.error_message = ""
        webhook_event.processed_at = timezone.now()
        webhook_event.updated_at = timezone.now()
        webhook_event.save()
    except Exception as e:
        logger.exception("Failed to process Stripe webhook", extra={"event_type": event_type})
        webhook_event.event_type = event_type
        webhook_event.payload = event_payload
        webhook_event.livemode = bool(event.get("livemode"))
        webhook_event.processed = False
        webhook_event.error_message = str(e)
        webhook_event.updated_at = timezone.now()
        webhook_event.save()
        return HttpResponse(status=500)

    return HttpResponse(status=200)

