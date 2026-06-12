"""Provider adapter registry for work item generation and submission."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .devops_service import DevOpsService


class WorkItemSubmissionAdapter(Protocol):
    provider: str

    def submit(
        self,
        *,
        service: "DevOpsService",
        user_id: str,
        work_items: List[Dict[str, Any]],
        project_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        ...


@dataclass(frozen=True)
class WorkItemProviderConfig:
    provider: str
    process_template: str
    submission_adapter: WorkItemSubmissionAdapter


class AzureSubmissionAdapter:
    provider = "azure"

    def submit(
        self,
        *,
        service: "DevOpsService",
        user_id: str,
        work_items: List[Dict[str, Any]],
        project_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        from integrations.services import get_integration_service

        return service._submit_to_azure_devops(
            user_id,
            work_items,
            project_config,
            get_integration_service(),
        )


class JiraSubmissionAdapter:
    provider = "jira"

    def submit(
        self,
        *,
        service: "DevOpsService",
        user_id: str,
        work_items: List[Dict[str, Any]],
        project_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        from integrations.services import get_integration_service

        return service._submit_to_jira(
            user_id,
            work_items,
            project_config,
            get_integration_service(),
        )


class AsanaSubmissionAdapter:
    provider = "asana"

    def submit(
        self,
        *,
        service: "DevOpsService",
        user_id: str,
        work_items: List[Dict[str, Any]],
        project_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        return service._submit_to_asana(work_items, project_config)


class LinearSubmissionAdapter:
    provider = "linear"

    def submit(
        self,
        *,
        service: "DevOpsService",
        user_id: str,
        work_items: List[Dict[str, Any]],
        project_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        from integrations.services import get_integration_service

        return service._submit_to_linear(
            user_id,
            work_items,
            project_config,
            get_integration_service(),
        )


WORK_ITEM_PROVIDER_CONFIGS: Dict[str, WorkItemProviderConfig] = {
    "azure": WorkItemProviderConfig(
        provider="azure",
        process_template="Agile",
        submission_adapter=AzureSubmissionAdapter(),
    ),
    "jira": WorkItemProviderConfig(
        provider="jira",
        process_template="Agile",
        submission_adapter=JiraSubmissionAdapter(),
    ),
    "asana": WorkItemProviderConfig(
        provider="asana",
        process_template="Agile",
        submission_adapter=AsanaSubmissionAdapter(),
    ),
    "linear": WorkItemProviderConfig(
        provider="linear",
        process_template="Agile",
        submission_adapter=LinearSubmissionAdapter(),
    ),
}


def get_supported_work_item_providers() -> List[str]:
    return list(WORK_ITEM_PROVIDER_CONFIGS.keys())


def get_provider_config(provider: str) -> WorkItemProviderConfig:
    normalized_provider = str(provider or "").lower()
    try:
        return WORK_ITEM_PROVIDER_CONFIGS[normalized_provider]
    except KeyError as exc:
        raise ValueError(f"Unsupported platform: {provider}") from exc


def get_default_process_template(provider: str) -> str:
    return get_provider_config(provider).process_template
