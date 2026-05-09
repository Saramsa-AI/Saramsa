"""
REST API views for organizational memory management.

Endpoints:
- POST   /api/memory/ingest/  — ingest a document into organizational memory
- DELETE /api/memory/         — delete tenant memory (optional source_type filter)
- GET    /api/memory/         — paginated metadata list (no raw content)
"""

import logging

from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apis.core.error_handlers import handle_service_errors
from apis.core.response import StandardResponse
from authentication.authentication import AppJWTAuthentication
from integrations.models import Project

from .enums import SourceType
from .models import OrganizationalMemory
from .services.ingestion_service import MemoryIngestionService

logger = logging.getLogger(__name__)


class MemoryIngestView(APIView):
    """
    POST /api/memory/ingest/

    Accepts {content, source_type, project_id, metadata (optional)} and ingests
    the document into organizational memory for the authenticated user's project.

    - Requires JWT authentication.
    - Returns 403 if the authenticated user does not own the project.
    - tenant_id is derived server-side from project.user_id; never accepted from
      the client request body.
    - Returns 201 with {"chunks_stored": N, "memory_ids": [...]} on success.
    """

    authentication_classes = [AppJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_service_errors
    def post(self, request):
        content = request.data.get("content")
        source_type_value = request.data.get("source_type")
        project_id = request.data.get("project_id")
        metadata = request.data.get("metadata") or {}

        # Validate required fields
        errors = []
        if not content:
            errors.append({"field": "content", "message": "This field is required."})
        if not source_type_value:
            errors.append({"field": "source_type", "message": "This field is required."})
        if not project_id:
            errors.append({"field": "project_id", "message": "This field is required."})

        if errors:
            return StandardResponse.validation_error(
                detail="One or more fields are invalid.",
                errors=errors,
                instance=request.path,
            )

        # Validate source_type is a recognised SourceType value
        valid_source_types = [choice[0] for choice in SourceType.choices]
        if source_type_value not in valid_source_types:
            return StandardResponse.validation_error(
                detail=f"Invalid source_type '{source_type_value}'. "
                       f"Must be one of: {', '.join(valid_source_types)}.",
                errors=[{"field": "source_type", "message": "Invalid value."}],
                instance=request.path,
            )

        source_type = SourceType(source_type_value)

        # Look up the project and verify ownership
        try:
            project = Project.objects.get(id=str(project_id))
        except Project.DoesNotExist:
            return StandardResponse.not_found(
                detail=f"Project with ID '{project_id}' was not found.",
                instance=request.path,
            )

        # Ownership check: project.user_id must match the authenticated user
        project_user_id = str(project.user_id) if project.user_id else None
        request_user_id = str(request.user.id) if request.user.id else None

        if project_user_id != request_user_id:
            return StandardResponse.forbidden(
                detail="You do not have permission to ingest memory for this project.",
                instance=request.path,
            )

        # Derive tenant_id server-side from project.user_id
        tenant_id = project_user_id

        # Call the ingestion service
        service = MemoryIngestionService()
        result = service.ingest_document(
            content=content,
            source_type=source_type,
            tenant_id=tenant_id,
            metadata=metadata,
        )

        return StandardResponse.created(
            data=result,
            message="Document ingested successfully.",
            instance=request.path,
        )


def _resolve_project_tenant(request, project_id):
    """
    Look up a project by ID and verify the requesting user owns it.

    Returns (project, tenant_id) on success, or a StandardResponse error response.
    """
    if not project_id:
        return None, StandardResponse.validation_error(
            detail="project_id is required.",
            errors=[{"field": "project_id", "message": "This field is required."}],
            instance=request.path,
        )
    try:
        project = Project.objects.get(id=str(project_id))
    except Project.DoesNotExist:
        return None, StandardResponse.not_found(
            detail=f"Project with ID '{project_id}' was not found.",
            instance=request.path,
        )

    project_user_id = str(project.user_id) if project.user_id else None
    request_user_id = str(request.user.id) if request.user.id else None

    if project_user_id != request_user_id:
        return None, StandardResponse.forbidden(
            detail="You do not have permission to access memory for this project.",
            instance=request.path,
        )

    return project_user_id, None  # (tenant_id, error)


class MemoryView(APIView):
    """
    GET  /api/memory/ — paginated metadata list (no raw content or embeddings)
    DELETE /api/memory/ — delete tenant memory with optional source_type filter

    Both methods require JWT authentication and project ownership.
    `project_id` is passed as a query param for both methods.
    """

    authentication_classes = [AppJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_service_errors
    def get(self, request):
        """
        GET /api/memory/?project_id=<id>&source_type=<type>&page=<n>&page_size=<n>

        Returns paginated memory entry metadata. Raw content and embeddings are excluded.
        """
        project_id = request.query_params.get("project_id")
        source_type_value = request.query_params.get("source_type")

        tenant_id, error = _resolve_project_tenant(request, project_id)
        if error:
            return error

        source_type = None
        if source_type_value:
            valid_source_types = [choice[0] for choice in SourceType.choices]
            if source_type_value not in valid_source_types:
                return StandardResponse.validation_error(
                    detail=f"Invalid source_type '{source_type_value}'. "
                           f"Must be one of: {', '.join(valid_source_types)}.",
                    errors=[{"field": "source_type", "message": "Invalid value."}],
                    instance=request.path,
                )
            source_type = SourceType(source_type_value)

        try:
            page = max(1, int(request.query_params.get("page", 1)))
            page_size = min(100, max(1, int(request.query_params.get("page_size", 20))))
        except (ValueError, TypeError):
            return StandardResponse.validation_error(
                detail="page and page_size must be positive integers.",
                errors=[],
                instance=request.path,
            )

        qs = OrganizationalMemory.objects.filter(tenant_id=tenant_id).order_by(
            "source_type", "source_document_id", "chunk_index"
        )
        if source_type:
            qs = qs.filter(source_type=source_type)

        total = qs.count()
        offset = (page - 1) * page_size
        entries = qs[offset: offset + page_size]

        items = [
            {
                "id": str(entry.id),
                "source_type": entry.source_type,
                "source_document_id": entry.source_document_id,
                "chunk_index": entry.chunk_index,
                "metadata": entry.metadata,
                "created_at": entry.created_at.isoformat() if hasattr(entry, "created_at") else None,
            }
            for entry in entries
        ]

        return StandardResponse.success(
            data={
                "items": items,
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": max(1, (total + page_size - 1) // page_size),
                },
            },
            message="Memory entries retrieved successfully.",
            instance=request.path,
        )

    @handle_service_errors
    def delete(self, request):
        """
        DELETE /api/memory/?project_id=<id>&source_type=<type>

        Deletes all memory for the tenant. Pass source_type to restrict deletion.
        Returns {"deleted": N}.
        """
        project_id = request.query_params.get("project_id")
        source_type_value = request.query_params.get("source_type")

        tenant_id, error = _resolve_project_tenant(request, project_id)
        if error:
            return error

        source_type = None
        if source_type_value:
            valid_source_types = [choice[0] for choice in SourceType.choices]
            if source_type_value not in valid_source_types:
                return StandardResponse.validation_error(
                    detail=f"Invalid source_type '{source_type_value}'. "
                           f"Must be one of: {', '.join(valid_source_types)}.",
                    errors=[{"field": "source_type", "message": "Invalid value."}],
                    instance=request.path,
                )
            source_type = SourceType(source_type_value)

        service = MemoryIngestionService()
        deleted_count = service.delete_tenant_memory(
            tenant_id=tenant_id,
            source_type=source_type,
        )

        return StandardResponse.success(
            data={"deleted": deleted_count},
            message="Memory deleted successfully.",
            instance=request.path,
        )
