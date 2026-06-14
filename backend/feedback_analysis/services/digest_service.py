"""
Weekly digest email service.

Gathers the past week's activity per project for each user and sends
a summary email: new feedback count, analyses completed, insights
generated, work items created, and review queue status.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db.models import Count, Q

from authentication.models import UserAccount
from feedback_analysis.models import Analysis, Insight, Upload
from integrations.models import Project, FeedbackSource
from work_items.models import UserStory

logger = logging.getLogger(__name__)


def _week_bounds(now: datetime | None = None):
    """Return (start, end) of the digest window — last 7 days."""
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(days=7)
    return start, end


def gather_project_stats(project: Project, since: datetime, until: datetime) -> dict[str, Any]:
    """Collect activity stats for a single project in [since, until)."""
    time_filter = Q(created_at__gte=since, created_at__lt=until)

    analyses = Analysis.objects.filter(project=project).filter(time_filter)
    analyses_count = analyses.count()

    insights_count = Insight.objects.filter(project=project).filter(time_filter).count()

    uploads_count = Upload.objects.filter(project=project).filter(time_filter).count()

    stories = UserStory.objects.filter(project=project).filter(time_filter)
    stories_count = stories.count()
    approved_count = stories.filter(status="approved").count()

    sources = FeedbackSource.objects.filter(project=project, status="active")
    active_sources = sources.count()

    return {
        "project_id": project.id,
        "project_name": project.name,
        "analyses_completed": analyses_count,
        "insights_generated": insights_count,
        "files_uploaded": uploads_count,
        "work_items_created": stories_count,
        "work_items_approved": approved_count,
        "active_sources": active_sources,
    }


def gather_user_digest(user: UserAccount, since: datetime, until: datetime) -> dict[str, Any]:
    """Build the full digest payload for one user.

    Scoped to the user's currently-active organization. Without this
    scope, a user who belongs to multiple workspaces would get a single
    digest that mixes activity across orgs.
    """
    profile = getattr(user, "profile", {}) or {}
    active_org_id = (
        profile.get("active_organization_id") if isinstance(profile, dict) else None
    )
    if not active_org_id:
        # Without an active workspace we can't safely build a single
        # digest — emitting unfiltered would mix every workspace the
        # user has ever joined into one email. Send an empty payload
        # in the same shape callers expect.
        return {
            "user_id": user.id,
            "email": user.email,
            "first_name": user.first_name or (user.email.split("@", 1)[0] if user.email else ""),
            "period_start": since.isoformat(),
            "period_end": until.isoformat(),
            "totals": {
                "analyses_completed": 0,
                "insights_generated": 0,
                "files_uploaded": 0,
                "work_items_created": 0,
                "work_items_approved": 0,
            },
            "projects": [],
        }
    projects = Project.objects.filter(
        user=user, status="active", organization_id=active_org_id
    )
    project_stats = []
    totals = {
        "analyses_completed": 0,
        "insights_generated": 0,
        "files_uploaded": 0,
        "work_items_created": 0,
        "work_items_approved": 0,
    }

    for project in projects:
        stats = gather_project_stats(project, since, until)
        # Skip projects with zero activity
        if any(stats[k] for k in totals):
            project_stats.append(stats)
            for k in totals:
                totals[k] += stats[k]

    return {
        "user_id": user.id,
        "email": user.email,
        "first_name": user.first_name or (user.email.split("@", 1)[0] if user.email else ""),
        "period_start": since.isoformat(),
        "period_end": until.isoformat(),
        "totals": totals,
        "projects": project_stats,
    }


def _build_html(digest: dict[str, Any]) -> str:
    """Render the digest as an HTML email body."""
    name = digest["first_name"]
    totals = digest["totals"]
    projects = digest["projects"]
    period_start = datetime.fromisoformat(digest["period_start"]).strftime("%b %d")
    period_end = datetime.fromisoformat(digest["period_end"]).strftime("%b %d, %Y")

    project_rows = ""
    for p in projects:
        project_rows += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;font-weight:600;">{p["project_name"]}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center;">{p["analyses_completed"]}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center;">{p["insights_generated"]}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center;">{p["work_items_created"]}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center;">{p["work_items_approved"]}</td>
        </tr>"""

    no_activity = ""
    if not projects:
        no_activity = '<p style="color:#888;font-style:italic;">No project activity this week.</p>'

    frontend_url = getattr(settings, "FRONTEND_BASE_URL", "") or "https://agent.saramsa.ai"

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f4f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f7;padding:32px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">

  <!-- Header -->
  <tr><td style="background:#6366f1;padding:24px 32px;">
    <h1 style="margin:0;color:#fff;font-size:22px;">Saramsa Weekly Digest</h1>
    <p style="margin:4px 0 0;color:#e0e0ff;font-size:13px;">{period_start} &ndash; {period_end}</p>
  </td></tr>

  <!-- Body -->
  <tr><td style="padding:24px 32px;">
    <p style="margin:0 0 16px;font-size:15px;color:#333;">Hi {name},</p>
    <p style="margin:0 0 20px;font-size:14px;color:#555;">Here&rsquo;s your weekly summary of feedback analysis activity.</p>

    <!-- Totals -->
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
    <tr>
      <td style="background:#f0f0ff;border-radius:8px;padding:16px;text-align:center;width:33%;">
        <div style="font-size:24px;font-weight:700;color:#6366f1;">{totals["analyses_completed"]}</div>
        <div style="font-size:12px;color:#666;margin-top:4px;">Analyses</div>
      </td>
      <td width="12"></td>
      <td style="background:#f0fff4;border-radius:8px;padding:16px;text-align:center;width:33%;">
        <div style="font-size:24px;font-weight:700;color:#22c55e;">{totals["insights_generated"]}</div>
        <div style="font-size:12px;color:#666;margin-top:4px;">Insights</div>
      </td>
      <td width="12"></td>
      <td style="background:#fff7ed;border-radius:8px;padding:16px;text-align:center;width:33%;">
        <div style="font-size:24px;font-weight:700;color:#f59e0b;">{totals["work_items_created"]}</div>
        <div style="font-size:12px;color:#666;margin-top:4px;">Work Items</div>
      </td>
    </tr>
    </table>

    {no_activity}

    <!-- Per-project breakdown -->
    {"" if not projects else '''
    <h3 style="margin:0 0 12px;font-size:14px;color:#333;">Project Breakdown</h3>
    <table width="100%" cellpadding="0" cellspacing="0" style="font-size:13px;color:#444;border:1px solid #eee;border-radius:6px;overflow:hidden;">
    <tr style="background:#fafafa;">
      <th style="padding:8px 12px;text-align:left;font-weight:600;">Project</th>
      <th style="padding:8px 12px;text-align:center;font-weight:600;">Analyses</th>
      <th style="padding:8px 12px;text-align:center;font-weight:600;">Insights</th>
      <th style="padding:8px 12px;text-align:center;font-weight:600;">Created</th>
      <th style="padding:8px 12px;text-align:center;font-weight:600;">Approved</th>
    </tr>''' + project_rows + '</table>'}

    <!-- CTA -->
    <div style="text-align:center;margin:28px 0 12px;">
      <a href="{frontend_url}" style="display:inline-block;background:#6366f1;color:#fff;padding:10px 28px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;">Open Saramsa</a>
    </div>
  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:16px 32px;background:#fafafa;border-top:1px solid #eee;">
    <p style="margin:0;font-size:11px;color:#999;text-align:center;">
      You&rsquo;re receiving this because you have an active Saramsa account.
      Manage preferences in Settings &rarr; Notifications.
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def _build_plain_text(digest: dict[str, Any]) -> str:
    """Plain-text fallback for the digest email."""
    name = digest["first_name"]
    totals = digest["totals"]
    projects = digest["projects"]
    period_start = datetime.fromisoformat(digest["period_start"]).strftime("%b %d")
    period_end = datetime.fromisoformat(digest["period_end"]).strftime("%b %d, %Y")

    lines = [
        f"Saramsa Weekly Digest — {period_start} to {period_end}",
        "",
        f"Hi {name},",
        "",
        "Here's your weekly summary:",
        f"  Analyses completed:  {totals['analyses_completed']}",
        f"  Insights generated:  {totals['insights_generated']}",
        f"  Files uploaded:      {totals['files_uploaded']}",
        f"  Work items created:  {totals['work_items_created']}",
        f"  Work items approved: {totals['work_items_approved']}",
        "",
    ]

    if projects:
        lines.append("Project Breakdown:")
        for p in projects:
            lines.append(
                f"  {p['project_name']}: "
                f"{p['analyses_completed']} analyses, "
                f"{p['insights_generated']} insights, "
                f"{p['work_items_created']} work items"
            )
        lines.append("")

    lines.append("Open Saramsa: " + (getattr(settings, "FRONTEND_BASE_URL", "") or "https://agent.saramsa.ai"))
    return "\n".join(lines)


def send_digest_email(user: UserAccount, digest: dict[str, Any]) -> bool:
    """Send the weekly digest to a single user. Returns True on success."""
    subject = "Your Saramsa Weekly Digest"
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@saramsa.ai")
    plain = _build_plain_text(digest)
    html = _build_html(digest)

    msg = EmailMultiAlternatives(subject, plain, from_email, [user.email])
    msg.attach_alternative(html, "text/html")

    try:
        msg.send(fail_silently=False)
        logger.info("Weekly digest sent", extra={"user_id": user.id})
        return True
    except Exception:
        logger.exception("Failed to send weekly digest", extra={"user_id": user.id})
        return False


def run_weekly_digest(now: datetime | None = None) -> dict[str, Any]:
    """
    Main entry point: gather stats and email every opted-in user.
    Called by the Celery beat task.
    """
    since, until = _week_bounds(now)
    users = UserAccount.objects.filter(is_active=True)

    sent = 0
    skipped = 0
    failed = 0

    for user in users:
        # Check opt-out preference
        prefs = user.extra or {}
        if not prefs.get("weekly_digest_enabled", True):
            skipped += 1
            continue

        digest = gather_user_digest(user, since, until)

        # Skip if zero total activity
        if not any(digest["totals"].values()):
            skipped += 1
            continue

        if send_digest_email(user, digest):
            sent += 1
        else:
            failed += 1

    logger.info(
        "Weekly digest run complete: sent=%d skipped=%d failed=%d",
        sent, skipped, failed,
    )
    return {"sent": sent, "skipped": skipped, "failed": failed}
