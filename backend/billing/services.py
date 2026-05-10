import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from django.conf import settings
from django.utils import timezone as dj_timezone

from authentication.models import UserAccount
from .models import BillingProfile

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {"active", "trialing"}


def _to_dt(unix_ts: Optional[int]) -> Optional[datetime]:
    if not unix_ts:
        return None
    return datetime.fromtimestamp(int(unix_ts), tz=timezone.utc)


class StripeBillingService:
    def __init__(self):
        self.secret_key = (getattr(settings, "STRIPE_SECRET_KEY", "") or "").strip()
        self.webhook_secret = (getattr(settings, "STRIPE_WEBHOOK_SECRET", "") or "").strip()
        self.default_price_id = (getattr(settings, "STRIPE_DEFAULT_PRICE_ID", "") or "").strip()
        self.frontend_base_url = (getattr(settings, "FRONTEND_BASE_URL", "") or "").rstrip("/")

    def _ensure_configured(self) -> None:
        if not self.secret_key:
            raise ValueError("Stripe is not configured: STRIPE_SECRET_KEY is missing.")

    def _stripe(self):
        try:
            import stripe  # type: ignore
        except Exception as e:
            raise ValueError("Stripe SDK is not installed. Run `pip install -r requirements.txt`.") from e
        stripe.api_key = self.secret_key
        return stripe

    def _resolve_user(self, user_id: str) -> UserAccount:
        user = UserAccount.objects.filter(id=str(user_id)).first()
        if not user:
            raise ValueError("User not found.")
        return user

    def _resolve_scope(self, user_id: str) -> tuple[UserAccount, Optional[str]]:
        user = self._resolve_user(user_id)
        active_org = ((user.profile or {}).get("active_organization_id") or "").strip() or None
        return user, active_org

    def _get_profile_for_scope(self, user_id: str) -> Optional[BillingProfile]:
        user, active_org = self._resolve_scope(user_id)
        if active_org:
            profile = BillingProfile.objects.filter(organization_id=active_org).order_by("-updated_at").first()
            if profile:
                return profile
            legacy = BillingProfile.objects.filter(user_id=str(user.id), organization_id="").order_by("-updated_at").first()
            if legacy:
                legacy.organization_id = active_org
                legacy.updated_at = dj_timezone.now()
                legacy.save(update_fields=["organization_id", "updated_at"])
                return legacy
            return None
        return BillingProfile.objects.filter(user_id=str(user.id), organization_id="").order_by("-updated_at").first()

    def _get_or_create_profile(self, user_id: str) -> BillingProfile:
        user, active_org = self._resolve_scope(user_id)
        if active_org:
            profile = BillingProfile.objects.filter(organization_id=active_org).order_by("-updated_at").first()
            if profile:
                return profile
            legacy = BillingProfile.objects.filter(user_id=str(user.id), organization_id="").order_by("-updated_at").first()
            if legacy:
                legacy.organization_id = active_org
                legacy.updated_at = dj_timezone.now()
                legacy.save(update_fields=["organization_id", "updated_at"])
                return legacy
            # Use get_or_create to avoid a race when two requests arrive
            # simultaneously for the same workspace's first BillingProfile.
            profile, _ = BillingProfile.objects.get_or_create(
                organization_id=active_org,
                defaults={"user_id": str(user.id)},
            )
            return profile

        profile, _ = BillingProfile.objects.get_or_create(user_id=str(user.id), organization_id="")
        return profile

    def _ensure_customer(self, user: UserAccount, profile: BillingProfile) -> str:
        self._ensure_configured()
        customer_id = (profile.stripe_customer_id or "").strip()
        if customer_id:
            return customer_id

        stripe = self._stripe()
        customer = stripe.Customer.create(
            email=user.email,
            name=f"{user.first_name} {user.last_name}".strip() or user.email,
            metadata={
                "user_id": str(user.id),
                "organization_id": profile.organization_id or "",
            },
        )
        profile.stripe_customer_id = customer.id
        profile.updated_at = dj_timezone.now()
        profile.save(update_fields=["stripe_customer_id", "updated_at"])
        return customer.id

    def _default_urls(self) -> Dict[str, str]:
        base = self.frontend_base_url or "http://localhost:3001"
        return {
            "success_url": f"{base}/settings?tab=integrations&billing=success&session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{base}/settings?tab=integrations&billing=cancel",
            "return_url": f"{base}/settings?tab=integrations",
        }

    def _validate_redirect_url(self, url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        from urllib.parse import urlparse
        parsed = urlparse(url)
        allowed_base = self.frontend_base_url or "http://localhost:3001"
        allowed_parsed = urlparse(allowed_base)
        if parsed.scheme not in ("http", "https"):
            return None
        if parsed.netloc != allowed_parsed.netloc:
            return None
        return url

    def create_checkout_session(
        self,
        user_id: str,
        price_id: Optional[str] = None,
        success_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._ensure_configured()
        user = self._resolve_user(user_id)
        profile = self._get_or_create_profile(str(user.id))
        customer_id = self._ensure_customer(user, profile)

        chosen_price = (price_id or self.default_price_id or "").strip()
        if not chosen_price:
            raise ValueError("No Stripe price configured. Set STRIPE_DEFAULT_PRICE_ID or pass price_id.")

        stripe = self._stripe()
        urls = self._default_urls()
        safe_success = self._validate_redirect_url(success_url) or urls["success_url"]
        safe_cancel = self._validate_redirect_url(cancel_url) or urls["cancel_url"]
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": chosen_price, "quantity": 1}],
            allow_promotion_codes=True,
            billing_address_collection="auto",
            success_url=safe_success,
            cancel_url=safe_cancel,
            client_reference_id=str(user.id),
            metadata={
                "user_id": str(user.id),
                "organization_id": profile.organization_id or "",
            },
            subscription_data={
                "metadata": {
                    "user_id": str(user.id),
                    "organization_id": profile.organization_id or "",
                }
            },
        )
        return {"session_id": session.id, "checkout_url": session.url}

    def create_billing_portal_session(self, user_id: str, return_url: Optional[str] = None) -> Dict[str, Any]:
        self._ensure_configured()
        user = self._resolve_user(user_id)
        profile = self._get_or_create_profile(str(user.id))
        customer_id = self._ensure_customer(user, profile)
        stripe = self._stripe()
        urls = self._default_urls()
        portal = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url or urls["return_url"],
        )
        return {"portal_url": portal.url}

    def cancel_subscription(self, user_id: str) -> Dict[str, Any]:
        self._ensure_configured()
        profile = self._get_profile_for_scope(user_id)
        if not profile or not profile.stripe_subscription_id:
            raise ValueError("No active subscription found.")
        stripe = self._stripe()
        sub = stripe.Subscription.modify(
            profile.stripe_subscription_id,
            cancel_at_period_end=True,
        )
        self._upsert_subscription_from_obj(sub, fallback_user_id=user_id)
        return {"cancel_at_period_end": True, "status": sub.get("status")}

    def resume_subscription(self, user_id: str) -> Dict[str, Any]:
        self._ensure_configured()
        profile = self._get_profile_for_scope(user_id)
        if not profile or not profile.stripe_subscription_id:
            raise ValueError("No active subscription found.")
        stripe = self._stripe()
        sub = stripe.Subscription.modify(
            profile.stripe_subscription_id,
            cancel_at_period_end=False,
        )
        self._upsert_subscription_from_obj(sub, fallback_user_id=user_id)
        return {"cancel_at_period_end": False, "status": sub.get("status")}

    def get_subscription_status(self, user_id: str) -> Dict[str, Any]:
        profile = self._get_profile_for_scope(user_id)
        if not profile:
            return {
                "has_subscription": False,
                "status": "inactive",
                "is_active": False,
                "stripe_customer_id": None,
                "stripe_subscription_id": None,
                "stripe_price_id": None,
                "current_period_end": None,
                "cancel_at_period_end": False,
            }
        status = (profile.subscription_status or "inactive").lower()
        return {
            "has_subscription": bool(profile.stripe_subscription_id),
            "status": status,
            "is_active": status in ACTIVE_STATUSES,
            "stripe_customer_id": profile.stripe_customer_id or None,
            "stripe_subscription_id": profile.stripe_subscription_id or None,
            "stripe_price_id": profile.stripe_price_id or None,
            "current_period_end": profile.current_period_end.isoformat() if profile.current_period_end else None,
            "cancel_at_period_end": profile.cancel_at_period_end,
        }

    def verify_webhook_event(self, payload: bytes, signature_header: str) -> Dict[str, Any]:
        self._ensure_configured()
        if not self.webhook_secret:
            raise ValueError("Stripe webhook is not configured: STRIPE_WEBHOOK_SECRET is missing.")
        stripe = self._stripe()
        event = stripe.Webhook.construct_event(payload, signature_header, self.webhook_secret)
        return event

    def _upsert_subscription_from_obj(self, subscription: Dict[str, Any], fallback_user_id: Optional[str] = None) -> None:
        customer_id = str(subscription.get("customer") or "")
        sub_id = str(subscription.get("id") or "")
        metadata = subscription.get("metadata") or {}
        organization_id = str(metadata.get("organization_id") or "")
        user_id = str(metadata.get("user_id") or fallback_user_id or "")
        profile = None
        if organization_id:
            profile = BillingProfile.objects.filter(organization_id=organization_id).order_by("-updated_at").first()
        if not user_id and customer_id:
            profile = profile or BillingProfile.objects.filter(stripe_customer_id=customer_id).first()
            user_id = profile.user_id if profile else ""
        if not user_id:
            logger.warning("Stripe subscription update skipped: user_id not resolvable (sub=%s)", sub_id)
            return

        profile = profile or self._get_or_create_profile(user_id)
        if customer_id:
            profile.stripe_customer_id = customer_id
        if organization_id:
            profile.organization_id = organization_id
        profile.stripe_subscription_id = sub_id
        profile.subscription_status = str(subscription.get("status") or "inactive").lower()

        items = (subscription.get("items") or {}).get("data") or []
        if items:
            price = items[0].get("price") or {}
            profile.stripe_price_id = str(price.get("id") or "")

        profile.current_period_start = _to_dt(subscription.get("current_period_start"))
        profile.current_period_end = _to_dt(subscription.get("current_period_end"))
        profile.cancel_at_period_end = bool(subscription.get("cancel_at_period_end"))
        profile.canceled_at = _to_dt(subscription.get("canceled_at"))
        profile.livemode = bool(subscription.get("livemode"))
        profile.metadata = {
            **(profile.metadata or {}),
            "last_subscription_event": str(subscription.get("status") or ""),
        }
        profile.updated_at = dj_timezone.now()
        profile.save()

    def process_event(self, event: Dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        payload_obj = (event.get("data") or {}).get("object") or {}

        if event_type == "checkout.session.completed":
            if payload_obj.get("mode") != "subscription":
                return
            user_id = (
                str(payload_obj.get("client_reference_id") or "")
                or str((payload_obj.get("metadata") or {}).get("user_id") or "")
            )
            sub_id = str(payload_obj.get("subscription") or "")
            if not sub_id:
                return
            stripe = self._stripe()
            sub = stripe.Subscription.retrieve(sub_id)
            self._upsert_subscription_from_obj(sub, fallback_user_id=user_id or None)
            return

        if event_type in {
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
        }:
            self._upsert_subscription_from_obj(payload_obj)
            return

        if event_type in {"invoice.paid", "invoice.payment_failed"}:
            sub_id = str(payload_obj.get("subscription") or "")
            if not sub_id:
                return
            stripe = self._stripe()
            sub = stripe.Subscription.retrieve(sub_id)
            self._upsert_subscription_from_obj(sub)
            return

        # Other events are intentionally ignored.
        return
