import logging
from typing import Dict, Optional

from apis.infrastructure.storage_service import storage_service
from ..repositories import IntegrationsRepository

logger = logging.getLogger(__name__)


PROMPT_TYPES = (
    "sentiment",
    "deep_analysis",
    "sentiment_confidence",
    "work_items_validation",
    "clarification",
)


def _load_default_prompts() -> Dict[str, str]:
    """Hardcoded defaults for each prompt type (UI prefill).

    Returns a {prompt_type: content} map. Failures are logged AND
    surfaced via the companion `_load_default_prompts_with_errors` so the
    admin UI can render "default unavailable" instead of silently
    showing an empty textarea (which an admin could mistake for "no
    default exists" and accidentally save an empty override over).
    """
    _errors, defaults = _load_default_prompts_with_errors()
    return defaults


def _load_default_prompts_with_errors() -> tuple[Dict[str, str], Dict[str, str]]:
    """Return (errors, defaults). errors maps prompt_type → reason for any
    type whose default could not be loaded."""
    errors: Dict[str, str] = {}
    defaults: Dict[str, str] = {}

    try:
        from apis.prompts.constants import get_prompt as _get_prompt
    except Exception as exc:
        logger.exception("apis.prompts.constants import failed; admin prompt UI will show 'default unavailable'")
        msg = f"Default unavailable: {exc}"
        for ptype in ("sentiment", "deep_analysis", "sentiment_confidence", "clarification"):
            defaults[ptype] = ""
            errors[ptype] = msg
    else:
        for ptype in ("sentiment", "deep_analysis", "sentiment_confidence", "clarification"):
            try:
                defaults[ptype] = _get_prompt(prompt_type=ptype)
            except Exception as exc:
                logger.exception("Failed to load default prompt for type=%s", ptype)
                defaults[ptype] = ""
                errors[ptype] = f"Default unavailable: {exc}"

    try:
        from apis.prompts.narration_prompt import _DEFAULT_TEMPLATE as _NARRATION_DEFAULT
        defaults["work_items_validation"] = _NARRATION_DEFAULT
    except Exception as exc:
        logger.exception("Failed to load narration default template; work_items_validation will be empty")
        defaults["work_items_validation"] = ""
        errors["work_items_validation"] = f"Default unavailable: {exc}"

    return errors, defaults


class PromptOverrideService:
    def __init__(self):
        self.repo = IntegrationsRepository()
        self.storage_service = storage_service

    def resolve_effective_prompt(
        self,
        *,
        prompt_type: str,
        default_prompt: str,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> str:
        resolved_org_id = organization_id
        if not resolved_org_id and project_id:
            project = self.storage_service.get_project_by_id_any(str(project_id))
            if isinstance(project, dict):
                resolved_org_id = project.get("organizationId")

        if resolved_org_id:
            org_override = self.repo.get_prompt_override(
                "organization",
                prompt_type,
                organization_id=str(resolved_org_id),
            )
            if org_override and org_override.get("content", "").strip():
                return org_override["content"]

        platform_override = self.repo.get_prompt_override("platform", prompt_type)
        if platform_override and platform_override.get("content", "").strip():
            return platform_override["content"]

        return default_prompt

    def list_admin_prompt_data(self, organization_id: Optional[str] = None) -> Dict:
        platform_prompts = {
            item["prompt_type"]: item
            for item in self.repo.list_prompt_overrides(scope="platform")
        }
        organization_prompts = {}
        if organization_id:
            organization_prompts = {
                item["prompt_type"]: item
                for item in self.repo.list_prompt_overrides(
                    scope="organization",
                    organization_id=str(organization_id),
                )
            }

        default_errors, default_prompts = _load_default_prompts_with_errors()
        return {
            "available_prompt_types": list(PROMPT_TYPES),
            "organizations": self.repo.list_all_organizations(),
            "platform_prompts": platform_prompts,
            "organization_prompts": organization_prompts,
            "selected_organization_id": organization_id,
            "default_prompts": default_prompts,
            "default_prompts_errors": default_errors,
        }

    def upsert_prompt(
        self,
        *,
        scope: str,
        prompt_type: str,
        content: str,
        updated_by_user_id: str,
        organization_id: Optional[str] = None,
    ) -> Dict:
        if scope not in ("platform", "organization"):
            raise ValueError("scope must be 'platform' or 'organization'.")
        if prompt_type not in PROMPT_TYPES:
            raise ValueError(f"prompt_type must be one of: {', '.join(PROMPT_TYPES)}")
        if not content or not content.strip():
            raise ValueError("content is required.")
        if scope == "organization" and not organization_id:
            raise ValueError("organization_id is required for organization scope.")

        result = self.repo.upsert_prompt_override(
            scope=scope,
            prompt_type=prompt_type,
            content=content.strip(),
            updated_by_user_id=updated_by_user_id,
            organization_id=organization_id,
        )
        self._invalidate_resolver_cache()
        return result

    def delete_prompt(self, *, scope: str, prompt_type: str, organization_id: Optional[str] = None) -> bool:
        if scope not in ("platform", "organization"):
            raise ValueError("scope must be 'platform' or 'organization'.")
        if prompt_type not in PROMPT_TYPES:
            raise ValueError(f"prompt_type must be one of: {', '.join(PROMPT_TYPES)}")
        if scope == "organization" and not organization_id:
            raise ValueError("organization_id is required for organization scope.")
        deleted = self.repo.delete_prompt_override(scope, prompt_type, organization_id=organization_id)
        if deleted:
            self._invalidate_resolver_cache()
        return deleted

    @staticmethod
    def _invalidate_resolver_cache() -> None:
        """Drop the apis.prompts.resolver in-process cache so admin edits
        take effect on the next LLM call instead of after the TTL.
        Failure is logged so a stuck cache shows up — admins would
        otherwise wonder why their saved override isn't taking effect."""
        try:
            from apis.prompts.resolver import invalidate_cache
            invalidate_cache()
        except Exception:
            logger.exception(
                "Failed to invalidate prompt resolver cache after override save/delete — "
                "admin edits will take up to 5s (the TTL) to propagate"
            )


_prompt_override_service = None


def get_prompt_override_service() -> PromptOverrideService:
    global _prompt_override_service
    if _prompt_override_service is None:
        _prompt_override_service = PromptOverrideService()
    return _prompt_override_service
