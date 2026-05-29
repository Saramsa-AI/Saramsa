"""
Taxonomy service for Phase-1: project-owned, versioned taxonomy.

Rules:
- Exactly ONE active taxonomy per project.
- LLMs may propose but never apply changes.
- Taxonomy changes are explicit, versioned, and archived (never deleted).
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import uuid
import logging

from django.db import transaction

from ..repositories import ProjectTaxonomyRepository
from ..models import Taxonomy
from .aspect_suggestion_service import get_aspect_suggestion_service
from asgiref.sync import async_to_sync

logger = logging.getLogger(__name__)


class TaxonomyService:
    """Service for managing project taxonomies."""

    def __init__(self):
        self.taxonomy_repo = ProjectTaxonomyRepository()

    def get_active_taxonomy(self, project_id: str, comments: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Resolve the active taxonomy for a project.

        Resolution order:
        1) Pinned taxonomy exists -> use it
        2) Active taxonomy exists AND healthy -> use it
        3) No taxonomy exists -> bootstrap once using GPT and save as version=1
        4) Taxonomy unhealthy -> use current taxonomy and flag DEGRADED
        """
        pinned = self.taxonomy_repo.get_pinned_by_project(project_id)
        if pinned:
            return pinned

        active = self.taxonomy_repo.get_active_by_project(project_id)
        if active:
            if self._is_taxonomy_healthy(active):
                return active
            # Unhealthy: keep it, flag degraded
            self.mark_taxonomy_degraded(project_id, active, metrics=None)
            return active

        # No taxonomy exists -> bootstrap once using GPT (requires comments)
        if comments is None:
            logger.warning("No active taxonomy found and no comments provided for bootstrap")
            return None
        aspect_service = get_aspect_suggestion_service()
        aspect_result = async_to_sync(aspect_service.suggest_aspects)(comments)
        suggested_aspects = aspect_result.get("suggested_aspects", [])
        return self.create_initial_taxonomy(project_id, suggested_aspects, source="gpt")

    def create_initial_taxonomy(self, project_id: str, aspects: List[str], source: str = "gpt",
                                domain: Optional[str] = None, is_locked: bool = False) -> Dict[str, Any]:
        """Create initial taxonomy for a project (version 1 or next).

        Args:
            project_id: Project ID
            aspects: List of aspect labels
            source: Origin of taxonomy (gpt, auto_regenerate, template, manual)
            domain: Optional domain name (e.g. "hospitality", "fintech")
            is_locked: Legacy admin-only hard pin. Auto-flows leave this False;
                cooldown handles flip-flop protection instead.
        """
        now = datetime.now(timezone.utc).isoformat()
        taxonomy_id = str(uuid.uuid4())

        # If this taxonomy was just produced by a full regeneration, treat the
        # creation moment as the regen marker; otherwise leave None so the very
        # first taxonomy for a project doesn't enter cooldown on creation.
        regen_now = now if source in ("auto_regenerate", "user_forced") else None

        # BUG 4 mitigation (code-level, no schema change): create the new active
        # row and archive prior actives inside ONE transaction, taking a row
        # lock on the project's existing taxonomies so concurrent uploads for
        # the same project can't interleave and leave two status="active" rows.
        # select_for_update() is a no-op on sqlite (tests) but serializes on
        # Postgres, which is where the race actually bites.
        with transaction.atomic():
            self._lock_project_taxonomies(project_id)
            next_version = max(1, self.taxonomy_repo.get_latest_version(project_id) + 1)
            taxonomy_doc = self._build_taxonomy_doc(
                taxonomy_id=taxonomy_id,
                project_id=project_id,
                aspects=aspects,
                source=source,
                domain=domain,
                is_locked=is_locked,
                version=next_version,
                now=now,
                regen_now=regen_now,
            )
            created = self.taxonomy_repo.create(taxonomy_doc)
            self.taxonomy_repo.archive_others_for_project(project_id, created.get("id"))
        return created

    @staticmethod
    def _lock_project_taxonomies(project_id: str) -> None:
        """Take a row lock on the project's taxonomies to serialize resolve-or-create.

        Must be called inside a transaction.atomic() block. Evaluating the
        queryset forces the SELECT ... FOR UPDATE. No-op on sqlite.
        """
        list(
            Taxonomy.objects
            .select_for_update()
            .filter(project_id=project_id, type="taxonomy")
            .values_list("id", flat=True)
        )

    @staticmethod
    def _build_taxonomy_doc(*, taxonomy_id, project_id, aspects, source, domain,
                            is_locked, version, now, regen_now) -> Dict[str, Any]:
        taxonomy_doc = {
            "id": taxonomy_id,
            "taxonomy_id": taxonomy_id,
            "project_id": project_id,
            "projectId": project_id,
            "version": version,
            "status": "active",
            "aspects": [
                {
                    "key": TaxonomyService._normalize_aspect_key(a),
                    "label": str(a),
                    "synonyms": [],
                    "usage_count": 0,  # Phase 3: track aspect usage
                    "last_used_at": None,
                }
                for a in aspects
                if a
            ],
            "source": source,
            "is_pinned": False,
            "is_locked": is_locked,
            "domain": domain or "unknown",
            "created_at": now,
            "updated_at": now,
            # Cooldown tracking: prevents auto-regen flip-flop without
            # permanently locking the taxonomy. A regen is allowed once both
            # gates have cleared: >=24h since last full regen AND >=3 uploads.
            "last_regenerated_at": regen_now,
            "uploads_since_regen": 0,
            "health_snapshot": {
                "last_unmapped_rate": None,
                "last_avg_aspects_per_comment": None,
                "last_confidence_p95": None,
            },
            "taxonomy_health": "UNKNOWN",
        }
        return taxonomy_doc

    def add_aspects_to_taxonomy(self, project_id: str, taxonomy: Dict[str, Any],
                                 new_aspects: List[str]) -> Dict[str, Any]:
        """Phase 3: Additive growth - add new aspects to existing taxonomy without replacing."""
        existing_keys = {a.get("key") for a in taxonomy.get("aspects", []) if isinstance(a, dict)}
        now = datetime.now(timezone.utc).isoformat()

        added = []
        for label in new_aspects:
            key = self._normalize_aspect_key(label)
            if key in existing_keys:
                continue
            added.append({
                "key": key,
                "label": str(label),
                "synonyms": [],
                "usage_count": 0,
                "last_used_at": None,
                "added_at": now,
            })

        if not added:
            logger.info(f"No new aspects to add - all {len(new_aspects)} already exist")
            return taxonomy

        taxonomy["aspects"] = taxonomy.get("aspects", []) + added
        taxonomy["updated_at"] = now
        taxonomy["aspect_count"] = len(taxonomy["aspects"])
        logger.info(f"Added {len(added)} new aspects to taxonomy: {[a['label'] for a in added]}")

        # Update in repository
        try:
            self.taxonomy_repo.update(taxonomy.get("id"), project_id, taxonomy)
        except Exception as e:
            logger.warning(f"Failed to persist additive taxonomy update: {e}")

        return taxonomy

    def update_aspect_usage(self, project_id: str, taxonomy: Dict[str, Any], used_aspects: List[str]) -> None:
        """Phase 3: Track which aspects were used in this analysis."""
        if not taxonomy or not used_aspects:
            return
        used_keys = {self._normalize_aspect_key(a) for a in used_aspects}
        now = datetime.now(timezone.utc).isoformat()
        for aspect in taxonomy.get("aspects", []):
            if isinstance(aspect, dict) and aspect.get("key") in used_keys:
                aspect["usage_count"] = aspect.get("usage_count", 0) + 1
                aspect["last_used_at"] = now
        try:
            self.taxonomy_repo.update(taxonomy.get("id"), project_id, taxonomy)
        except Exception as e:
            logger.warning(f"Failed to update aspect usage: {e}")

    def mark_taxonomy_degraded(self, project_id: str, taxonomy: Dict[str, Any], metrics: Optional[Dict[str, Any]]) -> None:
        """
        Mark taxonomy as degraded and store health snapshot.

        This is a warning-only signal; it must not trigger automatic regeneration.
        """
        if not taxonomy:
            return
        updated = taxonomy.copy()
        updated["taxonomy_health"] = "DEGRADED"
        if metrics:
            updated["health_snapshot"] = metrics
        updated["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.taxonomy_repo.update(updated.get("id"), project_id, updated)
        # TODO(phase-2): generate taxonomy suggestions from UNMAPPED comments.

    def pin_taxonomy(self, taxonomy_id: str, project_id: str) -> Optional[Dict[str, Any]]:
        """Pin a taxonomy and make it the active one."""
        taxonomy = self.taxonomy_repo.get_by_id(taxonomy_id, project_id)
        if not taxonomy:
            return None
        taxonomy["is_pinned"] = True
        taxonomy["status"] = "active"
        taxonomy["updated_at"] = datetime.now(timezone.utc).isoformat()
        updated = self.taxonomy_repo.update(taxonomy_id, project_id, taxonomy)
        if updated:
            self.taxonomy_repo.archive_others_for_project(project_id, taxonomy_id)
        return updated

    def archive_taxonomy(self, taxonomy_id: str, project_id: str) -> Optional[Dict[str, Any]]:
        """Archive a taxonomy (never delete)."""
        taxonomy = self.taxonomy_repo.get_by_id(taxonomy_id, project_id)
        if not taxonomy:
            return None
        taxonomy["status"] = "archived"
        taxonomy["is_pinned"] = False
        taxonomy["updated_at"] = datetime.now(timezone.utc).isoformat()
        return self.taxonomy_repo.update(taxonomy_id, project_id, taxonomy)

    def record_health_snapshot(self, project_id: str, taxonomy: Dict[str, Any], metrics: Dict[str, Any]) -> None:
        """Record health snapshot and set health status without changing taxonomy content."""
        if not taxonomy:
            return
        updated = taxonomy.copy()
        updated["health_snapshot"] = metrics
        updated["taxonomy_health"] = "HEALTHY" if self._is_healthy_metrics(metrics) else "DEGRADED"
        updated["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.taxonomy_repo.update(updated.get("id"), project_id, updated)

    def _is_taxonomy_healthy(self, taxonomy: Dict[str, Any]) -> bool:
        """Evaluate taxonomy health based on last recorded snapshot and age."""
        snapshot = taxonomy.get("health_snapshot") or {}
        if snapshot.get("last_unmapped_rate") is None or snapshot.get("last_avg_aspects_per_comment") is None:
            return True
        metrics = {
            "last_unmapped_rate": snapshot.get("last_unmapped_rate"),
            "last_avg_aspects_per_comment": snapshot.get("last_avg_aspects_per_comment"),
            "taxonomy_age_days": self._taxonomy_age_days(taxonomy),
        }
        return self._is_healthy_metrics(metrics)

    @staticmethod
    def _is_healthy_metrics(metrics: Dict[str, Any]) -> bool:
        """Health guardrails (deterministic)."""
        try:
            unmapped_rate = float(metrics.get("last_unmapped_rate"))
            avg_aspects = float(metrics.get("last_avg_aspects_per_comment"))
            age_days = float(metrics.get("taxonomy_age_days"))
        except Exception:
            return False
        return (
            unmapped_rate <= 0.15
            and avg_aspects <= 1.35
            and age_days <= 30
        )

    @staticmethod
    def _taxonomy_age_days(taxonomy: Dict[str, Any]) -> float:
        created_at = taxonomy.get("created_at") or taxonomy.get("createdAt")
        if not created_at:
            return 0.0
        try:
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except Exception:
            return 0.0
        return (datetime.now(timezone.utc) - created_dt).days

    # ------------------------------------------------------------------
    # Adaptive-regen cooldown
    # ------------------------------------------------------------------
    REGEN_COOLDOWN_HOURS = 24
    REGEN_COOLDOWN_UPLOADS = 3

    @classmethod
    def is_regen_cooldown_active(cls, taxonomy: Dict[str, Any]) -> bool:
        """Return True if a full regen happened too recently to do another.

        Cooldown is active when EITHER guard is still tripped:
          - fewer than REGEN_COOLDOWN_HOURS hours since the last regen, OR
          - fewer than REGEN_COOLDOWN_UPLOADS uploads since the last regen.

        A catastrophic-mismatch caller may bypass this; it exists only to
        damp out flip-flop between similar domains.
        """
        if not taxonomy:
            return False
        last_regen = taxonomy.get("last_regenerated_at")
        if not last_regen:
            return False
        try:
            last_dt = datetime.fromisoformat(str(last_regen).replace("Z", "+00:00"))
        except Exception:
            return False
        hours_since = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600.0
        uploads_since = int(taxonomy.get("uploads_since_regen") or 0)
        return hours_since < cls.REGEN_COOLDOWN_HOURS or uploads_since < cls.REGEN_COOLDOWN_UPLOADS

    def record_full_regeneration(self, project_id: str, taxonomy: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Mark a taxonomy as having just undergone full regeneration.

        Resets cooldown so the next adapt attempt has to wait for the
        configured window. Mutates ``taxonomy`` in place and returns it so the
        caller's dict reflects the persisted cooldown markers immediately.
        """
        if not taxonomy:
            return taxonomy
        now = datetime.now(timezone.utc).isoformat()
        return self._apply_cooldown_update(
            project_id, taxonomy,
            {"last_regenerated_at": now, "uploads_since_regen": 0},
            failure_msg="Failed to record full regeneration marker",
        )

    def increment_upload_counter(self, project_id: str, taxonomy: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Bump uploads_since_regen so the cooldown's upload gate can clear.

        BUG 1 fix: previously this incremented a throwaway ``taxonomy.copy()``
        and returned None, so the caller kept the pre-increment dict and the
        cooldown gate saw a stale counter every run. Now it reads the
        authoritative persisted counter, increments it, persists, AND mutates
        the caller's ``taxonomy`` dict in place (also returning it) so the
        regen decision downstream sees the fresh, incremented value.
        """
        if not taxonomy:
            return taxonomy
        # Read-modify-write against the persisted row inside a transaction so
        # concurrent uploads for the same project can't clobber each other's
        # increment (the persisted value is authoritative, not the in-memory
        # copy the caller happened to be holding).
        with transaction.atomic():
            self._lock_project_taxonomies(project_id)
            persisted = self.taxonomy_repo.get_by_id(taxonomy.get("id"), project_id)
            base = int((persisted or taxonomy).get("uploads_since_regen") or 0)
            return self._apply_cooldown_update(
                project_id, taxonomy,
                {"uploads_since_regen": base + 1},
                failure_msg="Failed to increment upload counter",
                _in_transaction=True,
            )

    def _apply_cooldown_update(self, project_id, taxonomy, fields, failure_msg,
                              _in_transaction: bool = False) -> Dict[str, Any]:
        """Persist cooldown bookkeeping fields and mirror them onto the caller's dict.

        Updating ``taxonomy`` in place is what makes the fresh values visible to
        the cooldown gate in the same request (the caller keeps using this very
        dict). Returns the same dict for call-site convenience.
        """
        try:
            updated = dict(taxonomy)
            updated.update(fields)
            self.taxonomy_repo.update(updated.get("id"), project_id, updated)
            # Reflect persisted values back onto the caller's live dict.
            taxonomy.update(fields)
        except Exception as e:
            logger.warning(f"{failure_msg}: {e}")
        return taxonomy

    @staticmethod
    def _normalize_aspect_key(label: str) -> str:
        return str(label).strip().lower().replace(" ", "_")


_taxonomy_service = None


def get_taxonomy_service() -> TaxonomyService:
    """Get the global taxonomy service instance."""
    global _taxonomy_service
    if _taxonomy_service is None:
        _taxonomy_service = TaxonomyService()
    return _taxonomy_service
