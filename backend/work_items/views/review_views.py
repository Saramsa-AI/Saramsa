"""
Review queue API views.

Endpoints for listing, approving, dismissing, snoozing, merging,
and batch-approving work item candidates.
"""

import logging
from rest_framework.views import APIView
from authentication.permissions import IsProjectEditor, IsProjectViewer
from apis.core.response import StandardResponse
from apis.core.error_handlers import handle_service_errors
from ..services import get_review_service

logger = logging.getLogger(__name__)


def _first_successful_push_result(push_result):
    results = (push_result or {}).get("results") or []
    for result in results:
        if result.get("success"):
            return result
    return {}


class ReviewQueueListView(APIView):
    """GET /api/work-items/review/ - list pending candidates."""
    permission_classes = [IsProjectViewer]

    @handle_service_errors
    def get(self, request):
        project_id = request.query_params.get('project_id')
        if not project_id:
            return StandardResponse.validation_error(detail="project_id is required.", instance=request.path)

        filters = {}
        for key in ('priority', 'feature_area', 'date_from', 'date_to'):
            val = request.query_params.get(key)
            if val:
                filters[key] = val

        status_filter = request.query_params.get('status', 'pending')
        service = get_review_service()

        if status_filter == 'pending':
            candidates = service.get_pending_candidates(project_id, filters or None)
        else:
            candidates = service.repo.get_candidates_by_status(project_id, status_filter)

        # DEBUG logging
        logger.info(f"ReviewQueue: project_id={project_id}, status={status_filter}, filters={filters}, count={len(candidates)}")
        if len(candidates) == 0:
            # Check if any candidates exist at all for this project
            all_candidates = service.repo.get_all_work_items_flat(project_id)
            logger.warning(f"ReviewQueue returned 0 candidates, but {len(all_candidates)} total candidates exist for project {project_id}")
            if all_candidates:
                logger.warning(f"Sample candidate: {all_candidates[0]}")

        return StandardResponse.success(data={
            'candidates': candidates,
            'count': len(candidates),
        })


class ReviewQueueStatsView(APIView):
    """GET /api/work-items/review/stats/ - queue statistics."""
    permission_classes = [IsProjectViewer]

    @handle_service_errors
    def get(self, request):
        project_id = request.query_params.get('project_id')
        if not project_id:
            return StandardResponse.validation_error(detail="project_id is required.", instance=request.path)

        stats = get_review_service().get_stats(project_id)
        logger.info(f"ReviewQueue Stats: project_id={project_id}, stats={stats}")
        return StandardResponse.success(data=stats)


class CandidateApproveView(APIView):
    """POST /api/work-items/review/approve/ - approve a candidate."""
    permission_classes = [IsProjectEditor]

    @handle_service_errors
    def post(self, request):
        candidate_id = request.data.get('candidate_id')
        project_id = request.data.get('project_id')
        edits = request.data.get('edits')

        if not candidate_id or not project_id:
            return StandardResponse.validation_error(
                detail="candidate_id and project_id are required.", instance=request.path)

        user_id = str(request.user.id)
        service = get_review_service()

        try:
            candidate = service.approve_candidate(candidate_id, user_id, project_id, edits)
        except ValueError as e:
            return StandardResponse.not_found(detail=str(e), instance=request.path)

        # Auto-push to external platform if configured
        push_result = None
        try:
            from integrations.services import get_project_service
            project_service = get_project_service()
            project_config = project_service.get_project(project_id, user_id)
            if project_config and project_config.get('auto_push_on_approve', True):
                from ..services import get_devops_service
                devops_service = get_devops_service()
                push_result = devops_service.submit_to_external_platform(
                    user_id=user_id,
                    work_items=[candidate],
                    platform=project_config.get('push_target_platform', 'azure'),
                    project_config=project_config,
                )
                first_result = _first_successful_push_result(push_result)
                if first_result:
                    push_updates = {
                        'push_status': 'pushed',
                        'external_id': first_result.get('work_item_id') or first_result.get('issue_key') or push_result.get('external_id'),
                        'external_url': first_result.get('url') or push_result.get('external_url'),
                        'external_platform': project_config.get('push_target_platform', 'azure'),
                        'pushed_at': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
                        'push_error': '',
                    }
                else:
                    push_updates = {
                        'push_status': 'failed',
                        'external_platform': project_config.get('push_target_platform', 'azure'),
                        'push_error': ((push_result.get('results') or [{}])[0]).get('error') or 'Push failed',
                    }
                service.repo.update_candidate_status(candidate_id, project_id, push_updates)
                candidate.update(push_updates)
        except Exception as e:
            logger.warning(f"Auto-push failed for candidate {candidate_id}: {e}")
            try:
                service.repo.update_candidate_status(candidate_id, project_id, {
                    'push_status': 'failed',
                    'push_error': str(e),
                })
            except Exception:
                pass

        return StandardResponse.success(
            data={'candidate': candidate, 'push_result': push_result},
            message="Candidate approved successfully",
        )


class CandidateDismissView(APIView):
    """POST /api/work-items/review/dismiss/ - dismiss a candidate."""
    permission_classes = [IsProjectEditor]

    @handle_service_errors
    def post(self, request):
        candidate_id = request.data.get('candidate_id')
        project_id = request.data.get('project_id')
        reason = request.data.get('reason')

        if not all([candidate_id, project_id, reason]):
            return StandardResponse.validation_error(
                detail="candidate_id, project_id, and reason are required.", instance=request.path)

        user_id = str(request.user.id)
        try:
            candidate = get_review_service().dismiss_candidate(candidate_id, user_id, project_id, reason)
        except ValueError as e:
            return StandardResponse.validation_error(detail=str(e), instance=request.path)

        return StandardResponse.success(data={'candidate': candidate}, message="Candidate dismissed")


class CandidateSnoozeView(APIView):
    """POST /api/work-items/review/snooze/ - snooze a candidate."""
    permission_classes = [IsProjectEditor]

    @handle_service_errors
    def post(self, request):
        candidate_id = request.data.get('candidate_id')
        project_id = request.data.get('project_id')
        snooze_days = request.data.get('snooze_days')

        if not all([candidate_id, project_id, snooze_days]):
            return StandardResponse.validation_error(
                detail="candidate_id, project_id, and snooze_days are required.", instance=request.path)

        try:
            snooze_days = int(snooze_days)
        except (TypeError, ValueError):
            return StandardResponse.validation_error(detail="snooze_days must be an integer.", instance=request.path)

        user_id = str(request.user.id)
        try:
            candidate = get_review_service().snooze_candidate(candidate_id, user_id, project_id, snooze_days)
        except ValueError as e:
            return StandardResponse.not_found(detail=str(e), instance=request.path)

        return StandardResponse.success(data={'candidate': candidate}, message="Candidate snoozed")


class CandidateMergeView(APIView):
    """POST /api/work-items/review/merge/ - merge two candidates."""
    permission_classes = [IsProjectEditor]

    @handle_service_errors
    def post(self, request):
        source_id = request.data.get('source_candidate_id')
        target_id = request.data.get('target_candidate_id')
        project_id = request.data.get('project_id')

        if not all([source_id, target_id, project_id]):
            return StandardResponse.validation_error(
                detail="source_candidate_id, target_candidate_id, and project_id are required.",
                instance=request.path)

        user_id = str(request.user.id)
        try:
            target = get_review_service().merge_candidates(source_id, target_id, user_id, project_id)
        except ValueError as e:
            return StandardResponse.not_found(detail=str(e), instance=request.path)

        return StandardResponse.success(data={'candidate': target}, message="Candidates merged")


class CandidateBatchApproveView(APIView):
    """POST /api/work-items/review/batch-approve/ - approve multiple candidates."""
    permission_classes = [IsProjectEditor]

    @handle_service_errors
    def post(self, request):
        candidate_ids = request.data.get('candidate_ids', [])
        project_id = request.data.get('project_id')

        if not candidate_ids or not project_id:
            return StandardResponse.validation_error(
                detail="candidate_ids and project_id are required.", instance=request.path)

        user_id = str(request.user.id)
        result = get_review_service().batch_approve(candidate_ids, user_id, project_id, auto_push=True)
        return StandardResponse.success(data=result, message=f"Batch approve complete: {result['approved']} approved")


class CandidateUpdateView(APIView):
    """PUT /api/work-items/review/update/ - update fields without changing status."""
    permission_classes = [IsProjectEditor]

    @handle_service_errors
    def put(self, request):
        candidate_id = request.data.get('candidate_id')
        project_id = request.data.get('project_id')
        updates = request.data.get('updates', {})

        if not candidate_id or not project_id:
            return StandardResponse.validation_error(
                detail="candidate_id and project_id are required.", instance=request.path)

        try:
            candidate = get_review_service().update_candidate_fields(candidate_id, project_id, updates)
        except ValueError as e:
            return StandardResponse.not_found(detail=str(e), instance=request.path)

        return StandardResponse.success(data={'candidate': candidate}, message="Candidate updated")


class CandidateRetryPushView(APIView):
    """POST /api/work-items/review/retry-push/ - retry a failed push."""
    permission_classes = [IsProjectEditor]

    @handle_service_errors
    def post(self, request):
        candidate_id = request.data.get('candidate_id')
        project_id = request.data.get('project_id')

        if not candidate_id or not project_id:
            return StandardResponse.validation_error(
                detail="candidate_id and project_id are required.", instance=request.path)

        user_id = str(request.user.id)
        try:
            candidate = get_review_service().retry_push(candidate_id, project_id, user_id)
        except ValueError as e:
            return StandardResponse.validation_error(detail=str(e), instance=request.path)

        return StandardResponse.success(data={'candidate': candidate}, message="Push retried successfully")
