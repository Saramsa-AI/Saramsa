
"""
Celery tasks for feedback analysis.
"""

import logging
import uuid
from datetime import datetime, timezone
from celery import shared_task, current_task

from .services.ingestion_schedule_service import get_ingestion_schedule_service

logger = logging.getLogger(__name__)


@shared_task(name="feedback_analysis.run_scheduled_ingestions")
def run_scheduled_ingestions():
    service = get_ingestion_schedule_service()
    results = service.run_due_schedules()
    logger.info(
        "Scheduled ingestion run completed: due=%s started=%s skipped=%s",
        results.get("due"),
        results.get("started"),
        results.get("skipped"),
    )
    return results


# ---------------------------------------------------------------------------
# Slack sync tasks  (SAR-42)
# ---------------------------------------------------------------------------

@shared_task(name="sync_slack_sources")
def sync_slack_sources():
    """Periodic task: fetch new messages from ALL active Slack sources."""
    from integrations.services.slack_service import get_slack_service
    from integrations.services.source_service import get_source_service
    from .repositories import AnalysisRepository

    source_service = get_source_service()
    slack_service = get_slack_service()
    analysis_repo = AnalysisRepository()

    sources = source_service.get_active_sources_by_provider("slack")
    total_messages = 0
    sources_synced = 0
    errors = []

    for source in sources:
        try:
            sync_result = _sync_one_source(source, slack_service, source_service, analysis_repo)
            total_messages += int(sync_result.get("messages_synced", 0))
            sources_synced += 1
        except Exception as e:
            logger.error(f"Error syncing source {source.get('id')}: {e}", exc_info=True)
            errors.append({"source_id": source.get("id"), "error": str(e)})

    logger.info(
        "sync_slack_sources done: sources=%d messages=%d errors=%d",
        sources_synced, total_messages, len(errors),
    )
    return {
        "sources_synced": sources_synced,
        "total_messages": total_messages,
        "errors": errors,
    }


@shared_task(name="sync_single_slack_source")
def sync_single_slack_source(source_id: str, user_id: str):
    """On-demand sync for a single source (Sync Now button)."""
    from integrations.services.slack_service import get_slack_service
    from integrations.services.source_service import get_source_service
    from .repositories import AnalysisRepository

    source_service = get_source_service()
    slack_service = get_slack_service()
    analysis_repo = AnalysisRepository()

    source = source_service.get_source_internal(source_id)
    if not source:
        logger.error(f"Source {source_id} not found for user {user_id}")
        return {"error": "source_not_found"}

    sync_result = _sync_one_source(source, slack_service, source_service, analysis_repo)
    return {"source_id": source_id, **sync_result}


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _sync_one_source(source, slack_service, source_service, analysis_repo):
    """Sync a single Slack feedback source. Returns number of new messages stored."""
    source_id = source["id"]
    user_id = source["userId"]
    project_id = source["projectId"]
    organization_id = source.get("organizationId")
    account_id = source["accountId"]
    config = source.get("config", {})
    channels = config.get("channels", [])
    oldest = config.get("last_sync_cursor")
    sync_cursors_by_channel = dict(config.get("last_sync_cursors") or {})

    # Collect existing source_ids for dedup
    existing_ids = _get_existing_source_ids(analysis_repo, project_id)

    total_new = 0
    analysis_triggered = False
    analysis_insight_id = None
    latest_ts = oldest
    analysis_comments = []
    last_analyzed_ts = config.get("last_analyzed_ts")
    last_analyzed_at = config.get("last_analyzed_at")
    analyzed_ts_by_channel = dict(config.get("last_analyzed_ts_by_channel") or {})

    def _max_ts_str(current, candidate):
        if not candidate:
            return current
        if not current:
            return candidate
        try:
            return candidate if float(candidate) > float(current) else current
        except Exception:
            return candidate if candidate > current else current

    for ch in channels:
        channel_id = ch.get("id", "")
        channel_name = ch.get("name", channel_id)
        channel_oldest = sync_cursors_by_channel.get(channel_id) or oldest

        raw_messages = slack_service.fetch_channel_messages(
            user_id, account_id, channel_id, oldest=channel_oldest, organization_id=organization_id,
        )

        # Deduplicate
        new_messages = slack_service.deduplicate_messages(raw_messages, existing_ids)
        if not new_messages:
            continue

        # Resolve user names
        token = slack_service._get_bot_token(user_id, account_id, organization_id=organization_id)
        user_ids = list({m["user_id"] for m in new_messages if m.get("user_id")})
        user_names = slack_service.resolve_user_names(user_ids, token)

        # Normalize
        feedback_items = slack_service.normalize_to_feedback(
            new_messages, channel_name, user_names
        )

        # Store feedback as analysis comments linked to the project
        if feedback_items:
            _store_feedback(analysis_repo, project_id, user_id, feedback_items)
            total_new += len(feedback_items)
            for m in new_messages:
                text = m.get("text")
                ts = m.get("slack_ts")
                if text and ts:
                    analysis_comments.append({"ts": ts, "text": text, "channel_id": channel_id})

            # Track latest timestamp for cursor
            for m in new_messages:
                latest_ts = _max_ts_str(latest_ts, m.get("slack_ts"))
                sync_cursors_by_channel[channel_id] = _max_ts_str(
                    sync_cursors_by_channel.get(channel_id),
                    m.get("slack_ts"),
                ) or sync_cursors_by_channel.get(channel_id)

            # Add to existing set so cross-channel dedup works within same run
            for m in new_messages:
                existing_ids.add(f"{m['channel_id']}_{m['slack_ts']}")

        logger.info(
            "Synced %d messages from #%s for project %s",
            len(feedback_items), channel_name, project_id,
        )

    # Update cursor
    now_iso = datetime.now(timezone.utc).isoformat()
    source_service.update_sync_cursor_internal(
        source_id,
        latest_ts,
        now_iso,
        cursors_by_channel=sync_cursors_by_channel,
    )

    # Auto-analyze newly synced Slack comments (create analysis run)
    if analysis_comments:
        try:
            cutoff_ts = None
            if last_analyzed_ts:
                try:
                    cutoff_ts = float(last_analyzed_ts)
                except Exception:
                    cutoff_ts = None
            elif last_analyzed_at:
                try:
                    cutoff_ts = datetime.fromisoformat(str(last_analyzed_at).replace("Z", "+00:00")).timestamp()
                except Exception:
                    cutoff_ts = None

            new_for_analysis = []
            for item in analysis_comments:
                try:
                    ts_val = float(item["ts"])
                except Exception:
                    continue
                channel_cutoff = analyzed_ts_by_channel.get(item.get("channel_id", ""))
                effective_cutoff = cutoff_ts
                if channel_cutoff:
                    try:
                        effective_cutoff = float(channel_cutoff)
                    except Exception:
                        effective_cutoff = cutoff_ts
                if effective_cutoff is None or ts_val > effective_cutoff:
                    new_for_analysis.append(item)

            if new_for_analysis:
                analysis_id = uuid.uuid4().hex
                comments_only = [c["text"] for c in new_for_analysis]
                enqueued_at = datetime.now(timezone.utc).isoformat()
                source_service.update_analysis_status_internal(
                    source_id,
                    "queued",
                    error=None,
                    enqueued_at=enqueued_at,
                )
                task_id = None
                try:
                    task_id = getattr(current_task.request, "id", None)
                except Exception:
                    task_id = None

                from .services import get_task_service

                task_service = get_task_service()
                analysis_result = task_service.process_feedback_background(
                    comments_only,
                    None,
                    user_id,
                    project_id,
                    analysis_id,
                    task_id=task_id,
                    suggested_aspects=None,
                )
                if isinstance(analysis_result, dict):
                    analysis_insight_id = analysis_result.get("insight_id")

                latest_new_ts = None
                try:
                    latest_new_ts = str(max(float(c["ts"]) for c in new_for_analysis))
                except Exception:
                    latest_new_ts = None

                source_service.update_analysis_cursor_internal(
                    source_id,
                    datetime.now(timezone.utc).isoformat(),
                    latest_new_ts,
                    analyzed_ts_by_channel={
                        **analyzed_ts_by_channel,
                        **{
                            str(chid): str(max(float(c["ts"]) for c in new_for_analysis if c.get("channel_id") == chid))
                            for chid in {c.get("channel_id") for c in new_for_analysis if c.get("channel_id")}
                        },
                    },
                )
                source_service.update_analysis_status_internal(
                    source_id,
                    "success",
                    error=None,
                )
                analysis_triggered = True
        except Exception as e:
            logger.error(f"Error triggering Slack analysis: {e}", exc_info=True)
            source_service.update_analysis_status_internal(
                source_id,
                "failed",
                error=str(e),
                failed_at=datetime.now(timezone.utc).isoformat(),
            )

    return {
        "messages_synced": total_new,
        "analysis_triggered": analysis_triggered,
        "insight_id": analysis_insight_id,
    }


def _get_existing_source_ids(analysis_repo, project_id):
    """Query existing source_ids for a project where source=slack."""
    try:
        from integrations.models import SlackFeedbackItem

        return set(
            SlackFeedbackItem.objects.filter(
                project_id=project_id,
            ).values_list("source_id", flat=True)
        )
    except Exception:
        return set()


def _store_feedback(analysis_repo, project_id, user_id, feedback_items):
    """Persist normalised feedback items into the database."""
    import uuid
    from integrations.models import SlackFeedbackItem

    for item in feedback_items:
        try:
            SlackFeedbackItem.objects.create(
                id=f"sf_{uuid.uuid4().hex[:10]}",
                project_id=project_id,
                user_id=user_id,
                source_id=item["source_id"],
                comment=item["comment"],
                source_channel=item["source_channel"],
                author=item["author"],
                feedback_created_at=item["created_at"],
            )
        except Exception as e:
            logger.warning(f"Failed to store feedback item: {e}")


@shared_task(name="send_weekly_digest")
def send_weekly_digest():
    """Weekly task: send digest emails summarising the past 7 days."""
    from .services.digest_service import run_weekly_digest
    result = run_weekly_digest()
    logger.info(
        "Weekly digest: sent=%s skipped=%s failed=%s",
        result.get("sent"), result.get("skipped"), result.get("failed"),
    )
    return result


@shared_task(name="unsnooze_expired_candidates")
def unsnooze_expired_candidates():
    """Auto-resurface snoozed candidates when their snooze period expires."""
    from work_items.services import get_review_service
    review_service = get_review_service()
    count = review_service.unsnooze_expired()
    logger.info("Unsnoozed %d candidates", count)
    return count
