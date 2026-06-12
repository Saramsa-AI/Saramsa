"""Asana integration service: target configuration and Insight push.

Handles:
- Configure-target: ensures the saramsa_insight_id custom field exists on
  a customer's Asana project, caches the field GIDs in the integration's
  config, and persists the saramsa-project ↔ asana-project link.
- Push insight: idempotent create-or-update of an Asana task per Saramsa
  Insight. Idempotency is keyed on the saramsa_insight_id custom field
  value (Asana has no native idempotency-key header for PAT clients).

Outbound calls go through httpx with simple 429 / 5xx retry. We don't
wrap a full client class yet; do that in C3 when webhook reconciliation
adds the next round of HTTP needs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx
from django.conf import settings
from django.db import transaction
from django.utils import timezone as dj_timezone

from feedback_analysis.models import Insight
from ..models import AsanaTaskMapping, IntegrationAccount, Project
from ..repositories import IntegrationsRepository
from .encryption_service import get_encryption_service

logger = logging.getLogger(__name__)

ASANA_API_BASE = "https://app.asana.com/api/1.0"
SARAMSA_INSIGHT_FIELD_NAME = "saramsa_insight_id"
HASHED_FIELDS_FOR_RECONCILIATION = ("name", "notes", "completed", "custom_fields")


class AsanaService:
    """Asana push pipeline. Inbound webhook handling lands in C3."""

    def __init__(self) -> None:
        self.integrations_repo = IntegrationsRepository()
        self.encryption = get_encryption_service()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def configure_target(
        self,
        *,
        user_id: str,
        organization_id: str,
        saramsa_project_id: str,
        asana_project_gid: str,
    ) -> Dict[str, Any]:
        """Bind a Saramsa project to an Asana project, ensuring the
        saramsa_insight_id custom field exists and is cached."""
        integration_row, pat_token = self._load_integration(organization_id)

        custom_field_gids = self._ensure_custom_fields(
            pat_token=pat_token,
            workspace_gid=self._workspace_gid(integration_row),
            asana_project_gid=asana_project_gid,
        )

        config = dict(integration_row.config or {})
        targets = dict(config.get("asanaProjectTargets") or {})
        existing_target = dict(targets.get(saramsa_project_id) or {})
        existing_target.update({
            "asana_project_gid": asana_project_gid,
            "custom_field_gids": custom_field_gids,
            "configured_at": datetime.now(timezone.utc).isoformat(),
        })
        targets[saramsa_project_id] = existing_target
        config["asanaProjectTargets"] = targets
        integration_row.config = config
        integration_row.updated_at = dj_timezone.now()
        integration_row.save(update_fields=["config", "updated_at"])

        return targets[saramsa_project_id]

    def push_insight(self, *, insight_id: str) -> Dict[str, Any]:
        """Create or update an Asana task for the given Insight.

        Resolution order:
        1. Existing AsanaTaskMapping → GET task; PUT if changed.
        2. Mapping points at deleted task (404) → search by custom field.
        3. Search hit → adopt the task, persist mapping.
        4. Search miss → POST /tasks, persist mapping.

        Concurrent pushes of the *same* insight are serialized with a row
        lock: without it, two requests could both miss the mapping+search and
        both POST a task — the unique constraint would reject the second
        mapping, but the duplicate remote task would already exist. The lock
        is held across the Asana HTTP round-trips; contention is per-insight
        and negligible at this scale.
        """
        with transaction.atomic():
            # Lock only the insight row (of=("self",)); the join to project is
            # not locked. select_for_update is a no-op on sqlite (tests).
            Insight.objects.select_for_update(of=("self",)).filter(id=insight_id).first()
            return self._push_insight_locked(insight_id=insight_id)

    def _push_insight_locked(self, *, insight_id: str) -> Dict[str, Any]:
        insight = Insight.objects.select_related("project").filter(id=insight_id).first()
        if not insight:
            raise ValueError(f"Insight {insight_id} not found")
        if not insight.project:
            raise ValueError(f"Insight {insight_id} has no project")

        organization_id = insight.project.organization_id
        if not organization_id:
            raise ValueError(f"Insight {insight_id} project has no organization")

        integration_row, pat_token = self._load_integration(organization_id)
        target = self._target_for_project(integration_row, insight.project_id)
        asana_project_gid = target["asana_project_gid"]
        insight_field_gid = target["custom_field_gids"][SARAMSA_INSIGHT_FIELD_NAME]

        existing_mapping = AsanaTaskMapping.objects.filter(insight=insight).first()
        body = self._task_body_for_insight(insight, asana_project_gid, insight_field_gid)

        if existing_mapping:
            current = self._fetch_task(pat_token, existing_mapping.asana_task_gid)
            if current is not None:
                return self._update_if_changed(
                    pat_token=pat_token,
                    mapping=existing_mapping,
                    current_task=current,
                    desired_body=body,
                )
            existing_mapping.delete()

        adopted = self._search_by_insight_id(
            pat_token=pat_token,
            asana_project_gid=asana_project_gid,
            insight_field_gid=insight_field_gid,
            insight_id=insight.id,
        )
        if adopted:
            mapping = self._persist_mapping(
                insight=insight,
                integration_row=integration_row,
                asana_task_gid=adopted["gid"],
                asana_project_gid=asana_project_gid,
            )
            return self._update_if_changed(
                pat_token=pat_token,
                mapping=mapping,
                current_task=adopted,
                desired_body=body,
            )

        created = self._create_task(pat_token, body)
        mapping = self._persist_mapping(
            insight=insight,
            integration_row=integration_row,
            asana_task_gid=created["gid"],
            asana_project_gid=asana_project_gid,
        )
        return {
            "asana_task_gid": created["gid"],
            "permalink_url": created.get("permalink_url", ""),
            "action": "created",
            "mapping_id": mapping.id,
        }

    def subscribe_webhook(self, *, saramsa_project_id: str) -> Dict[str, Any]:
        """Create an Asana webhook on the configured target's project.

        The handshake (X-Hook-Secret echo) happens on the receiver side
        during this POST — Asana calls our webhook URL synchronously
        before returning. We rely on the integration's webhook_secret
        already being persisted by the receiver before we read the
        response here."""
        integration_row, pat_token = self._load_integration_for_project(saramsa_project_id)
        target = self._target_for_project(integration_row, saramsa_project_id)
        existing_webhook_gid = str(target.get("webhook_gid") or "").strip()
        if existing_webhook_gid:
            return {"webhook_gid": existing_webhook_gid, "active": True}
        subscribe_token = secrets.token_urlsafe(24)

        config = dict(integration_row.config or {})
        targets = dict(config.get("asanaProjectTargets") or {})
        target_entry = dict(targets.get(saramsa_project_id) or {})
        target_entry["webhook_subscribe_token"] = subscribe_token
        targets[saramsa_project_id] = target_entry
        config["asanaProjectTargets"] = targets
        integration_row.config = config
        integration_row.updated_at = dj_timezone.now()
        integration_row.save(update_fields=["config", "updated_at"])

        target_url = self._webhook_target_url(
            saramsa_project_id=saramsa_project_id,
            subscribe_token=subscribe_token,
        )

        response = self._request(
            method="POST",
            url=f"{ASANA_API_BASE}/webhooks",
            pat_token=pat_token,
            json={
                "data": {
                    "resource": target["asana_project_gid"],
                    "target": target_url,
                    "filters": [
                        {"resource_type": "task", "action": "changed"},
                        {"resource_type": "task", "action": "added"},
                        {"resource_type": "task", "action": "removed"},
                    ],
                }
            },
        )
        webhook = response.get("data") or {}
        webhook_gid = webhook.get("gid")
        if not webhook_gid:
            raise ValueError("Asana webhook create returned no GID")

        integration_row.refresh_from_db(fields=["config", "updated_at"])
        config = dict(integration_row.config or {})
        targets = dict(config.get("asanaProjectTargets") or {})
        target_entry = dict(targets.get(saramsa_project_id) or {})
        target_entry["webhook_gid"] = webhook_gid
        targets[saramsa_project_id] = target_entry
        config["asanaProjectTargets"] = targets
        integration_row.config = config
        integration_row.updated_at = dj_timezone.now()
        integration_row.save(update_fields=["config", "updated_at"])

        return {"webhook_gid": webhook_gid, "active": webhook.get("active", True)}

    def submit_work_items(
        self, *, saramsa_project_id: str, work_items: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Create or update Asana tasks for reviewed work items."""
        integration_row, pat_token = self._load_integration_for_project(saramsa_project_id)
        target = self._target_for_project(integration_row, saramsa_project_id)
        asana_project_gid = target["asana_project_gid"]

        results: List[Dict[str, Any]] = []
        for work_item in work_items:
            story_id = str(work_item.get("id") or "")
            if not story_id:
                results.append({
                    "success": False,
                    "story_id": "",
                    "error": "Work item is missing an id.",
                })
                continue

            desired_body = self._task_body_for_work_item(work_item, asana_project_gid)
            existing_gid = str(work_item.get("external_id") or "").strip()

            try:
                if existing_gid:
                    current_task = self._fetch_task(pat_token, existing_gid)
                    if current_task is not None:
                        updated = self._update_task_for_work_item(
                            pat_token=pat_token,
                            asana_task_gid=existing_gid,
                            current_task=current_task,
                            desired_body=desired_body,
                        )
                        results.append({
                            "success": True,
                            "story_id": story_id,
                            "work_item_id": updated["asana_task_gid"],
                            "url": updated["permalink_url"],
                            "action": updated["action"],
                        })
                        continue

                created = self._create_task(pat_token, desired_body)
                results.append({
                    "success": True,
                    "story_id": story_id,
                    "work_item_id": created.get("gid"),
                    "url": created.get("permalink_url") or self._asana_task_url(
                        asana_project_gid, created.get("gid")
                    ),
                    "action": "created",
                })
            except Exception as exc:
                results.append({
                    "success": False,
                    "story_id": story_id,
                    "error": str(exc),
                })

        successful = [result for result in results if result.get("success")]
        failed = [result for result in results if not result.get("success")]
        first_success = successful[0] if successful else {}

        return {
            "success": len(failed) == 0,
            "submitted_count": len(successful),
            "failed_count": len(failed),
            "platform": "asana",
            "project_gid": asana_project_gid,
            "external_id": first_success.get("work_item_id"),
            "external_url": first_success.get("url"),
            "results": results,
        }

    def apply_event(self, *, saramsa_project_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
        """Reconcile a single Asana webhook event back to the linked Insight.

        Events are notifications, not state — we always GET the resource
        fresh. Skips work when the canonicalized state hash matches what
        we last saw, so duplicate deliveries are cheap.
        """
        resource = event.get("resource") or {}
        if resource.get("resource_type") != "task":
            return {"action": "skipped", "reason": "non-task-resource"}

        asana_task_gid = resource.get("gid")
        if not asana_task_gid:
            return {"action": "skipped", "reason": "no-gid"}

        integration_row, pat_token = self._load_integration_for_project(saramsa_project_id)

        mapping = AsanaTaskMapping.objects.select_related("insight").filter(
            asana_task_gid=asana_task_gid,
            integration_id=integration_row.id,
        ).first()
        if not mapping:
            return {"action": "skipped", "reason": "untracked-task"}

        # The webhook is bound to one Saramsa project; only reconcile a mapping
        # whose insight belongs to that project, even though several projects
        # may share one org-level integration.
        if str(mapping.insight.project_id) != str(saramsa_project_id):
            return {"action": "skipped", "reason": "project-mismatch"}

        try:
            current = self._fetch_task(pat_token, asana_task_gid)
        except _AsanaError as exc:
            logger.warning("Asana fetch failed during webhook apply: %s", exc)
            return {"action": "error", "reason": str(exc)}

        if current is None:
            return {"action": "skipped", "reason": "task-not-found"}

        new_hash = self._inbound_state_hash(current)
        if new_hash == mapping.last_known_state_hash and mapping.last_known_state_hash:
            return {"action": "noop"}

        insight = mapping.insight
        payload = dict(insight.payload or {})
        if current.get("name"):
            payload["title"] = current["name"]
        if "notes" in current:
            payload["summary"] = current.get("notes") or ""
        if current.get("completed"):
            payload["status"] = "resolved"
        elif current.get("completed") is False:
            payload["status"] = "open"
        insight.payload = payload
        insight.save(update_fields=["payload", "updated_at"])

        mapping.last_known_state_hash = new_hash
        mapping.last_synced_at = dj_timezone.now()
        mapping.save(update_fields=["last_known_state_hash", "last_synced_at", "updated_at"])

        return {
            "action": "applied",
            "asana_task_gid": asana_task_gid,
            "insight_id": insight.id,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_integration_for_project(
        self, saramsa_project_id: str
    ) -> tuple[IntegrationAccount, str]:
        project = Project.objects.filter(id=saramsa_project_id).first()
        if not project or not project.organization_id:
            raise ValueError(f"Project {saramsa_project_id} not found or has no organization")
        return self._load_integration(project.organization_id)

    def _webhook_target_url(self, *, saramsa_project_id: str, subscribe_token: str) -> str:
        base = (getattr(settings, "ASANA_WEBHOOK_TARGET_URL", "") or "").rstrip("/")
        if not base:
            raise ValueError("ASANA_WEBHOOK_TARGET_URL is not configured")
        return f"{base}/{saramsa_project_id}/?{urlencode({'token': subscribe_token})}"

    def _inbound_state_hash(self, task: Dict[str, Any]) -> str:
        canonical = json.dumps(
            {
                "name": task.get("name"),
                "notes": task.get("notes"),
                "completed": task.get("completed"),
                "custom_fields": [
                    {"gid": cf.get("gid"), "text_value": cf.get("text_value"),
                     "enum_value": (cf.get("enum_value") or {}).get("gid"),
                     "number_value": cf.get("number_value")}
                    for cf in (task.get("custom_fields") or [])
                ],
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _load_integration(self, organization_id: str) -> tuple[IntegrationAccount, str]:
        row = IntegrationAccount.objects.filter(
            organization_id=str(organization_id),
            provider="asana",
            is_active=True,
        ).first()
        if not row:
            raise ValueError(f"Asana integration not configured for organization {organization_id}")
        encrypted = (row.credentials or {}).get("tokenEncrypted")
        if not encrypted:
            raise ValueError("Asana integration has no stored token")
        return row, self.encryption.decrypt_token(encrypted)

    def _workspace_gid(self, integration_row: IntegrationAccount) -> str:
        config = integration_row.config or {}
        meta = config.get("metadata") or {}
        gid = meta.get("workspaceGid")
        if not gid:
            raise ValueError("Asana integration has no workspace GID")
        return gid

    def _target_for_project(
        self, integration_row: IntegrationAccount, saramsa_project_id: str
    ) -> Dict[str, Any]:
        targets = (integration_row.config or {}).get("asanaProjectTargets") or {}
        target = targets.get(saramsa_project_id)
        if not target:
            raise ValueError(
                f"No Asana target configured for project {saramsa_project_id}"
            )
        if not target.get("custom_field_gids", {}).get(SARAMSA_INSIGHT_FIELD_NAME):
            raise ValueError(
                f"Asana target for {saramsa_project_id} missing {SARAMSA_INSIGHT_FIELD_NAME} field"
            )
        return target

    def _ensure_custom_fields(
        self, *, pat_token: str, workspace_gid: str, asana_project_gid: str
    ) -> Dict[str, str]:
        """Make sure saramsa_insight_id text custom field exists on the
        project. Returns {field_name: gid}."""
        settings_response = self._request(
            method="GET",
            url=f"{ASANA_API_BASE}/projects/{asana_project_gid}/custom_field_settings",
            pat_token=pat_token,
            params={"opt_fields": "custom_field.name,custom_field.gid"},
        )
        existing = {}
        for setting in (settings_response.get("data") or []):
            cf = setting.get("custom_field") or {}
            if cf.get("name") and cf.get("gid"):
                existing[cf["name"]] = cf["gid"]

        result: Dict[str, str] = {}
        if SARAMSA_INSIGHT_FIELD_NAME in existing:
            result[SARAMSA_INSIGHT_FIELD_NAME] = existing[SARAMSA_INSIGHT_FIELD_NAME]
            return result

        created = self._request(
            method="POST",
            url=f"{ASANA_API_BASE}/custom_fields",
            pat_token=pat_token,
            json={
                "data": {
                    "workspace": workspace_gid,
                    "name": SARAMSA_INSIGHT_FIELD_NAME,
                    "type": "text",
                    "description": "Saramsa insight ID — managed by Saramsa, do not edit.",
                }
            },
        )
        new_gid = (created.get("data") or {}).get("gid")
        if not new_gid:
            raise ValueError("Asana custom field create returned no GID")

        self._request(
            method="POST",
            url=f"{ASANA_API_BASE}/projects/{asana_project_gid}/addCustomFieldSetting",
            pat_token=pat_token,
            json={"data": {"custom_field": new_gid, "is_important": False}},
        )
        result[SARAMSA_INSIGHT_FIELD_NAME] = new_gid
        return result

    def _task_body_for_insight(
        self, insight: Insight, asana_project_gid: str, insight_field_gid: str
    ) -> Dict[str, Any]:
        payload = insight.payload or {}
        title = payload.get("title") or payload.get("theme") or f"Insight {insight.id[:12]}"
        notes = payload.get("summary") or payload.get("description") or ""
        return {
            "name": title,
            "notes": notes,
            "projects": [asana_project_gid],
            "custom_fields": {insight_field_gid: insight.id},
        }

    def _task_body_for_work_item(
        self, work_item: Dict[str, Any], asana_project_gid: str
    ) -> Dict[str, Any]:
        title = str(work_item.get("title") or "Untitled task").strip()
        description = str(
            work_item.get("description")
            or work_item.get("acceptance_criteria")
            or work_item.get("acceptance")
            or ""
        ).strip()
        acceptance = str(
            work_item.get("acceptance_criteria") or work_item.get("acceptance") or ""
        ).strip()
        priority = str(work_item.get("priority") or "medium").strip().title()
        feature_area = str(
            work_item.get("feature_area") or work_item.get("featurearea") or ""
        ).strip()
        tags = work_item.get("tags") or work_item.get("labels") or []
        if not isinstance(tags, list):
            tags = [str(tags)]

        notes_sections = []
        if description:
            notes_sections.append(description)
        if acceptance and acceptance not in description:
            notes_sections.append(f"Acceptance criteria:\n{acceptance}")
        metadata_lines = [f"Priority: {priority}"]
        if feature_area:
            metadata_lines.append(f"Feature area: {feature_area}")
        if tags:
            metadata_lines.append(
                "Tags: " + ", ".join(str(tag).strip() for tag in tags if str(tag).strip())
            )
        notes_sections.append("\n".join(metadata_lines))

        return {
            "name": title,
            "notes": "\n\n".join(section for section in notes_sections if section).strip(),
            "projects": [asana_project_gid],
        }

    def _fetch_task(self, pat_token: str, gid: str) -> Optional[Dict[str, Any]]:
        try:
            response = self._request(
                method="GET",
                url=f"{ASANA_API_BASE}/tasks/{gid}",
                pat_token=pat_token,
                params={"opt_fields": "name,notes,completed,custom_fields,permalink_url"},
            )
            return response.get("data")
        except _AsanaNotFound:
            return None

    def _search_by_insight_id(
        self,
        *,
        pat_token: str,
        asana_project_gid: str,
        insight_field_gid: str,
        insight_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Find an existing Asana task whose saramsa_insight_id custom
        field equals insight_id. Returns the first match or None."""
        response = self._request(
            method="GET",
            url=f"{ASANA_API_BASE}/projects/{asana_project_gid}/tasks",
            pat_token=pat_token,
            params={
                "opt_fields": f"name,notes,completed,custom_fields.gid,custom_fields.text_value",
                "limit": 100,
            },
        )
        for task in response.get("data") or []:
            for cf in task.get("custom_fields") or []:
                if cf.get("gid") == insight_field_gid and cf.get("text_value") == insight_id:
                    return task
        return None

    def _create_task(self, pat_token: str, body: Dict[str, Any]) -> Dict[str, Any]:
        response = self._request(
            method="POST",
            url=f"{ASANA_API_BASE}/tasks",
            pat_token=pat_token,
            params={"opt_fields": "gid,permalink_url"},
            json={"data": body},
        )
        return response.get("data") or {}

    def _update_task_for_work_item(
        self,
        *,
        pat_token: str,
        asana_task_gid: str,
        current_task: Dict[str, Any],
        desired_body: Dict[str, Any],
    ) -> Dict[str, Any]:
        if (
            current_task.get("name") == desired_body.get("name")
            and (current_task.get("notes") or "") == (desired_body.get("notes") or "")
        ):
            return {
                "asana_task_gid": asana_task_gid,
                "permalink_url": current_task.get("permalink_url")
                or self._asana_task_url(
                    desired_body["projects"][0], asana_task_gid
                ),
                "action": "noop",
            }

        response = self._request(
            method="PUT",
            url=f"{ASANA_API_BASE}/tasks/{asana_task_gid}",
            pat_token=pat_token,
            params={"opt_fields": "gid,permalink_url"},
            json={
                "data": {
                    "name": desired_body["name"],
                    "notes": desired_body["notes"],
                }
            },
        )
        task = response.get("data") or {}
        return {
            "asana_task_gid": task.get("gid") or asana_task_gid,
            "permalink_url": task.get("permalink_url")
            or self._asana_task_url(desired_body["projects"][0], asana_task_gid),
            "action": "updated",
        }

    @staticmethod
    def _asana_task_url(asana_project_gid: str, asana_task_gid: str | None) -> str:
        if not asana_project_gid or not asana_task_gid:
            return ""
        return f"https://app.asana.com/0/{asana_project_gid}/{asana_task_gid}"

    def _update_if_changed(
        self,
        *,
        pat_token: str,
        mapping: AsanaTaskMapping,
        current_task: Dict[str, Any],
        desired_body: Dict[str, Any],
    ) -> Dict[str, Any]:
        new_hash = self._state_hash(desired_body)
        if mapping.last_known_state_hash == new_hash and mapping.last_known_state_hash:
            return {
                "asana_task_gid": mapping.asana_task_gid,
                "permalink_url": current_task.get("permalink_url", ""),
                "action": "noop",
                "mapping_id": mapping.id,
            }

        self._request(
            method="PUT",
            url=f"{ASANA_API_BASE}/tasks/{mapping.asana_task_gid}",
            pat_token=pat_token,
            json={
                "data": {
                    "name": desired_body["name"],
                    "notes": desired_body["notes"],
                    "custom_fields": desired_body["custom_fields"],
                }
            },
        )
        mapping.last_known_state_hash = new_hash
        mapping.last_synced_at = dj_timezone.now()
        mapping.save(update_fields=["last_known_state_hash", "last_synced_at", "updated_at"])
        return {
            "asana_task_gid": mapping.asana_task_gid,
            "permalink_url": current_task.get("permalink_url", ""),
            "action": "updated",
            "mapping_id": mapping.id,
        }

    def _persist_mapping(
        self,
        *,
        insight: Insight,
        integration_row: IntegrationAccount,
        asana_task_gid: str,
        asana_project_gid: str,
    ) -> AsanaTaskMapping:
        mapping = AsanaTaskMapping.objects.create(
            id=f"atm_{uuid.uuid4().hex[:12]}",
            organization_id=insight.project.organization_id,
            insight=insight,
            integration=integration_row,
            asana_task_gid=asana_task_gid,
            asana_project_gid=asana_project_gid,
            last_synced_at=dj_timezone.now(),
        )
        return mapping

    def _state_hash(self, body: Dict[str, Any]) -> str:
        canonical = json.dumps(
            {k: body.get(k) for k in HASHED_FIELDS_FOR_RECONCILIATION if k in body},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------

    def _request(
        self,
        *,
        method: str,
        url: str,
        pat_token: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        max_attempts: int = 3,
    ) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {pat_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        last_exc: Optional[Exception] = None
        for attempt in range(max_attempts):
            try:
                response = httpx.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json,
                    timeout=30,
                )
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt + 1 == max_attempts:
                    raise
                time.sleep(min(2 ** attempt, 10))
                continue

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "5") or "5")
                logger.warning("Asana 429 rate limited, retrying after %ss", retry_after)
                time.sleep(retry_after)
                continue
            if 500 <= response.status_code < 600 and attempt + 1 < max_attempts:
                time.sleep(min(2 ** attempt, 10))
                continue
            if response.status_code == 404:
                raise _AsanaNotFound(f"Asana 404: {url}")
            if response.status_code >= 400:
                raise _AsanaError(
                    f"Asana {response.status_code}: {response.text}",
                    status_code=response.status_code,
                )
            return response.json() or {}

        raise _AsanaError("Asana retries exhausted", status_code=0) from last_exc


class _AsanaError(Exception):
    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class _AsanaNotFound(_AsanaError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=404)


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------
_asana_service: Optional[AsanaService] = None


def get_asana_service() -> AsanaService:
    global _asana_service
    if _asana_service is None:
        _asana_service = AsanaService()
    return _asana_service
