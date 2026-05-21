"""Workspace invitation lifecycle.

Pattern modelled on Linear / Vercel / Notion:
- An admin or owner creates an invite for an email + role.
- We issue a single-use opaque token, email it out, and store a row
  with status='pending' and an expiry of INVITE_TTL_DAYS (default 7).
- Re-inviting the same email refreshes the existing row's token and
  expiry instead of inserting a duplicate (the partial unique index
  on (organization_id, email) where status='pending' enforces this).
- The invitee accepts via the token. Acceptance is locked to the
  exact email the invite was sent to — Bob can't accept Alice's invite.
- Once accepted, status flips to 'accepted' and the token is dead.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


INVITE_TTL_DAYS = int(os.getenv("INVITE_TTL_DAYS", "7"))
ALLOWED_ROLES = {"viewer", "member", "admin"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def _hash_token(token: str) -> str:
    """SHA-256 hex digest of a plaintext invite token.

    Used for both create-time storage and lookup-time matching. The
    plaintext token is generated via `secrets.token_urlsafe(32)` which
    already has 256 bits of entropy, so a simple SHA-256 is sufficient
    here — no per-row salt needed (the token IS the salt).
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class OrganizationInviteService:
    def __init__(self):
        from .organization_service import OrganizationService

        self.org_service = OrganizationService()

    def _resolve_active_invite(self, organization_id: str, email: str):
        from ..models import OrganizationInvite

        return (
            OrganizationInvite.objects
            .filter(organization_id=organization_id, email=email, status="pending")
            .first()
        )

    def create_invite(
        self,
        *,
        organization_id: str,
        actor_user_id: str,
        email: str,
        role: str = "member",
    ) -> Dict[str, Any]:
        from ..models import Organization, OrganizationInvite
        from authentication.repositories import UserRepository

        actor_membership = self.org_service.require_membership(organization_id, actor_user_id)
        if not self.org_service.has_min_role(actor_membership, "admin"):
            raise ValueError("Only workspace admins can invite members.")

        if role not in ALLOWED_ROLES:
            raise ValueError(f"Role must be one of: {', '.join(sorted(ALLOWED_ROLES))}")

        normalised_email = _normalise_email(email)
        if not normalised_email:
            raise ValueError("Email is required.")

        organization = Organization.objects.filter(id=organization_id).first()
        if not organization:
            raise ValueError("Workspace not found.")

        # If the email already belongs to a member, no point sending an invite.
        user_repo = UserRepository()
        existing_user = user_repo.get_by_email(normalised_email)
        if existing_user:
            existing_membership = self.org_service.repo.get_organization_membership(
                organization_id, str(existing_user["id"])
            )
            if existing_membership:
                raise ValueError("That user is already a member of this workspace.")

        # Generate plaintext token + its hash. We persist ONLY the hash;
        # the plaintext is returned to the caller exactly once below for
        # delivery to the invitee (via email or shown-once URL in the UI).
        # See migration 0006_hash_invite_tokens for why.
        token = secrets.token_urlsafe(32)
        token_hash = _hash_token(token)
        expires_at = _now_utc() + timedelta(days=INVITE_TTL_DAYS)

        existing = self._resolve_active_invite(organization_id, normalised_email)
        if existing:
            # Re-invite refreshes token + expiry + role + invited_by.
            # Note: we overwrite `token_hash`, and explicitly set the
            # legacy `token` column to None even though it's the same
            # plaintext-vs-hash story — keeps the DB clean of plaintext
            # so a future RemoveField is safe.
            existing.token = None
            existing.token_hash = token_hash
            existing.role = role
            existing.expires_at = expires_at
            existing.invited_by_id = str(actor_user_id)
            existing.status = "pending"
            existing.accepted_at = None
            existing.accepted_by_id = None
            existing.updated_at = _now_utc()
            existing.save()
            invite = existing
        else:
            invite = OrganizationInvite.objects.create(
                id=f"inv_{uuid.uuid4().hex[:16]}",
                organization=organization,
                email=normalised_email,
                role=role,
                token=None,
                token_hash=token_hash,
                invited_by_id=str(actor_user_id),
                status="pending",
                expires_at=expires_at,
            )

        # Stash the plaintext token on the invite instance (not the DB row)
        # so _to_dict can include it in the response. The instance attr
        # is never persisted — it's just a one-shot carrier from this
        # method to the caller.
        invite._plaintext_token_for_response = token
        return self._to_dict(invite, include_token=True, organization=organization)

    def list_pending(self, organization_id: str, actor_user_id: str) -> List[Dict[str, Any]]:
        from ..models import OrganizationInvite

        actor_membership = self.org_service.require_membership(organization_id, actor_user_id)
        if not self.org_service.has_min_role(actor_membership, "admin"):
            raise ValueError("Only workspace admins can view pending invites.")
        return [
            self._to_dict(inv)
            for inv in OrganizationInvite.objects
                .filter(organization_id=organization_id, status="pending")
                .order_by("-created_at")
        ]

    def revoke_invite(self, invite_id: str, actor_user_id: str) -> Dict[str, Any]:
        from ..models import OrganizationInvite

        invite = OrganizationInvite.objects.filter(id=invite_id).first()
        if not invite:
            raise ValueError("Invite not found.")
        actor_membership = self.org_service.require_membership(
            str(invite.organization_id), actor_user_id
        )
        if not self.org_service.has_min_role(actor_membership, "admin"):
            raise ValueError("Only workspace admins can revoke invites.")
        if invite.status != "pending":
            raise ValueError("Only pending invites can be revoked.")
        invite.status = "revoked"
        invite.updated_at = _now_utc()
        invite.save(update_fields=["status", "updated_at"])
        return self._to_dict(invite)

    def get_by_token(self, token: str) -> Dict[str, Any]:
        """Public lookup so the signup/accept page can show what org
        the invitee is being invited into. Returns minimal info; never
        leaks who else is in the org.

        Lookup is by `token_hash` — the plaintext token is hashed first
        so we never run a query against the deprecated plaintext column.
        Existing invite URLs in the wild still work because the same
        plaintext token hashes to the value backfilled by migration 0006.
        """
        from ..models import Organization, OrganizationInvite

        invite = OrganizationInvite.objects.filter(token_hash=_hash_token(token)).first()
        if not invite:
            raise ValueError("This invite link is invalid.")
        if invite.status == "accepted":
            raise ValueError("This invite has already been used.")
        if invite.status == "revoked":
            raise ValueError("This invite has been revoked.")
        if invite.expires_at <= _now_utc():
            # `<=` not `<` so an invite that expires exactly at `now` is
            # treated as expired. The strict-less-than version had a
            # zero-width window where an invite at exactly its TTL would
            # still validate — flagged in the auth audit.
            raise ValueError("This invite has expired.")
        organization = Organization.objects.filter(id=invite.organization_id).first()
        return {
            "id": invite.id,
            "email": invite.email,
            "role": invite.role,
            "organization": {
                "id": organization.id if organization else invite.organization_id,
                "name": organization.name if organization else None,
                "slug": organization.slug if organization else None,
            },
            "expires_at": invite.expires_at.isoformat(),
        }

    def accept_invite(self, *, token: str, user_id: str, user_email: str) -> Dict[str, Any]:
        """Add the user as a member of the inviting org and mark the
        invite consumed. Validates email lock + status + expiry inside
        an atomic block so concurrent accepts can't double-membership."""
        from django.db import transaction

        from ..models import OrganizationInvite

        normalised_user_email = _normalise_email(user_email)

        with transaction.atomic():
            invite = (
                OrganizationInvite.objects.select_for_update()
                .filter(token_hash=_hash_token(token))
                .first()
            )
            if not invite:
                raise ValueError("This invite link is invalid.")
            if invite.status == "accepted":
                raise ValueError("This invite has already been used.")
            if invite.status == "revoked":
                raise ValueError("This invite has been revoked.")
            if invite.expires_at < _now_utc():
                raise ValueError("This invite has expired.")
            if invite.email != normalised_user_email:
                raise ValueError(
                    "This invite was sent to a different email. Sign in with the invited account."
                )

            self.org_service.repo.upsert_organization_membership(
                str(invite.organization_id),
                str(user_id),
                invite.role,
                actor_id=str(invite.invited_by_id) if invite.invited_by_id else str(user_id),
            )

            invite.status = "accepted"
            invite.accepted_at = _now_utc()
            invite.accepted_by_id = str(user_id)
            invite.updated_at = _now_utc()
            invite.save(update_fields=[
                "status", "accepted_at", "accepted_by_id", "updated_at",
            ])

        return {
            "organization_id": str(invite.organization_id),
            "role": invite.role,
        }

    def _to_dict(self, invite, *, include_token: bool = False, organization=None) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": invite.id,
            "organization_id": str(invite.organization_id),
            "email": invite.email,
            "role": invite.role,
            "status": invite.status,
            "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
            "invited_by_user_id": str(invite.invited_by_id) if invite.invited_by_id else None,
            "created_at": invite.created_at.isoformat() if invite.created_at else None,
        }
        if include_token:
            # Prefer the one-shot plaintext stashed by create_or_get_invite
            # — the DB row's `token` column is NULL since migration 0006.
            # Falls back to invite.token only for backwards-compat with any
            # legacy code path that still populated the plaintext column
            # (post-migration this is always None for new rows).
            plaintext = getattr(invite, "_plaintext_token_for_response", None) or invite.token
            data["token"] = plaintext
            if organization is None:
                from ..models import Organization
                organization = Organization.objects.filter(id=invite.organization_id).first()
            if organization:
                data["organization"] = {
                    "id": organization.id,
                    "name": organization.name,
                    "slug": organization.slug,
                }
        return data


_invite_service: Optional[OrganizationInviteService] = None


def get_organization_invite_service() -> OrganizationInviteService:
    global _invite_service
    if _invite_service is None:
        _invite_service = OrganizationInviteService()
    return _invite_service
