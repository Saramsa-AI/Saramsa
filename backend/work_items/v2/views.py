"""Preview endpoint for the V2 work-item pipeline. No persistence."""

import csv
import io
import logging

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import JSONParser, FormParser, MultiPartParser

from apis.core.response import StandardResponse

from .pipeline import run_v2_pipeline

logger = logging.getLogger(__name__)

MAX_ROWS = 2000


class WorkItemV2PreviewView(APIView):
    """POST /api/work-items/v2/generate-preview/

    Accepts either multipart with a `file` (.csv) or a JSON body
    {"rows": [...], "company_name": "..."}. Runs the experimental V2
    pipeline and returns the full PipelineResult. Nothing is persisted.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        rows = None

        uploaded = request.FILES.get("file")
        if uploaded is not None:
            if not uploaded.name.lower().endswith(".csv"):
                return StandardResponse.validation_error(
                    detail="Only .csv files are supported.",
                    instance=request.path,
                )
            try:
                text_stream = io.TextIOWrapper(uploaded.file, encoding="utf-8-sig")
                rows = list(csv.DictReader(text_stream))
            except Exception:
                logger.exception("v2 preview: failed to parse uploaded CSV")
                return StandardResponse.validation_error(
                    detail="Could not parse the uploaded CSV file.",
                    instance=request.path,
                )
        else:
            body_rows = request.data.get("rows") if isinstance(request.data, dict) else None
            if isinstance(body_rows, list):
                rows = [r for r in body_rows if isinstance(r, dict)]

        if not rows:
            return StandardResponse.validation_error(
                detail="Provide a CSV `file` (multipart) or a JSON body with a non-empty `rows` array.",
                instance=request.path,
            )
        if len(rows) > MAX_ROWS:
            return StandardResponse.validation_error(
                detail=f"Too many rows ({len(rows)}); maximum is {MAX_ROWS}.",
                instance=request.path,
            )

        company_name = "Company"
        if isinstance(request.data, dict) and request.data.get("company_name"):
            company_name = str(request.data.get("company_name"))

        user_id = getattr(request.user, "id", None)
        project_id = None
        if isinstance(request.data, dict) and request.data.get("project_id"):
            project_id = str(request.data.get("project_id"))

        try:
            result = run_v2_pipeline(
                rows,
                company_name=company_name,
                user_id=str(user_id) if user_id is not None else None,
                project_id=project_id,
            )
        except Exception as exc:
            logger.exception("v2 preview pipeline failed")
            return StandardResponse.validation_error(
                detail=f"V2 pipeline failed: {exc}",
                instance=request.path,
            )

        return StandardResponse.success(data=result.to_dict())
