"""Sync orchestrator for the V2 work-item pipeline.

Stage order: parse -> discover -> assign -> per-theme analyst -> priority ->
guardrails. LLM access goes through aiCore's generate_completions, imported
LAZILY inside the client factory so schemas/priority/guardrails stay
importable without Django settings.
"""

import logging
import os
import threading
import time
import uuid
from typing import Dict, List, Optional

from .analyst import propose_work_items, theme_qualifies
from .clustering import assign_comments, discover_themes
from .evidence import parse_rows
from .guardrails import compute_segments, validate_and_merge
from .schemas import (
    EvidenceRecord,
    PipelineResult,
    ProposedItem,
    Theme,
    UNTHEMED_KEY,
)

logger = logging.getLogger(__name__)

TASK_TYPE = "v2_workitems_experiment"

# Concurrent analyst calls (thread-pool fan-out, same pattern as
# llm_aspect_service which runs 20; analyst calls are heavier so default lower).
ANALYST_CONCURRENCY = int(os.getenv("V2_ANALYST_CONCURRENCY", "8"))


class _LLMClient:
    """Sync callable over the async generate_completions helper.

    Mirrors narration_service._call_generate_completions: safe when already
    inside an event loop (runs in a one-off thread)."""

    def __init__(self, user_id: Optional[str], project_id: Optional[str]):
        self.user_id = user_id
        self.project_id = project_id
        self.calls = 0
        self._calls_lock = threading.Lock()

    def __call__(self, prompt: str, max_tokens: int) -> str:
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        from asgiref.sync import async_to_sync
        from aiCore.services.completion_service import generate_completions

        with self._calls_lock:
            self.calls += 1

        def _run():
            return async_to_sync(generate_completions)(
                prompt,
                max_tokens=max_tokens,
                user_id=self.user_id,
                project_id=self.project_id,
                task_type=TASK_TYPE,
            )

        def _run_in_context():
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return _run()
            with ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(_run).result()

        # Transient Azure disconnects ("Server disconnected without sending a
        # response") killed a whole theme in run 1 — retry once after a pause.
        try:
            content, _usage = _run_in_context()
        except ConnectionError:
            import time as _time

            logger.warning("v2 LLM call hit a connection error; retrying once in 5s")
            _time.sleep(5)
            content, _usage = _run_in_context()
        return content


def _build_themes(
    theme_specs,
    records: List[EvidenceRecord],
    assignments: Dict[str, Dict],
) -> List[Theme]:
    themes = [
        Theme(key=spec.key, label=spec.label, description=spec.description)
        for spec in theme_specs
    ]
    unthemed = Theme(
        key=UNTHEMED_KEY,
        label="Unthemed",
        description="Comments that did not fit any discovered theme (kept, not lost).",
    )
    by_key = {theme.key: theme for theme in themes}
    by_key[UNTHEMED_KEY] = unthemed

    for record in records:
        assignment = assignments.get(record.id) or {"themes": [UNTHEMED_KEY], "sentiment": "unknown"}
        sentiment = assignment.get("sentiment", "unknown")
        for key in assignment.get("themes") or [UNTHEMED_KEY]:
            theme = by_key.get(key)
            if theme is None:
                continue
            theme.comment_ids.append(record.id)
            theme.sentiment_counts[sentiment] = theme.sentiment_counts.get(sentiment, 0) + 1

    if unthemed.comment_ids:
        themes.append(unthemed)
    return themes


def run_v2_pipeline(
    rows: List[Dict],
    company_name: str = "Company",
    user_id: Optional[str] = None,
    project_id: Optional[str] = None,
    options: Optional[Dict] = None,
) -> PipelineResult:
    """Run the full V2 pipeline. Preview-only: no persistence."""
    options = options or {}
    llm = _LLMClient(user_id=user_id, project_id=project_id)
    timings_ms: Dict[str, float] = {}
    total_start = time.perf_counter()

    # Stage: parse
    start = time.perf_counter()
    records, source_summary = parse_rows(rows)
    records_by_id = {record.id: record for record in records}
    timings_ms["parse"] = round((time.perf_counter() - start) * 1000, 1)

    if not records:
        timings_ms["total"] = round((time.perf_counter() - total_start) * 1000, 1)
        return PipelineResult(
            run_id=f"v2_{uuid.uuid4().hex[:12]}",
            source_summary=source_summary,
            themes=[],
            work_items=[],
            guardrail_report={
                "dropped_no_citation": 0,
                "invalid_ids_stripped": 0,
                "merged_pairs": [],
                "vocab_fixes": 0,
            },
            llm_calls=0,
            timings_ms=timings_ms,
        )

    # Stage: discover (LLM 1a)
    start = time.perf_counter()
    theme_specs, identified_domain = discover_themes(records, company_name, llm)
    source_summary["identified_domain"] = identified_domain
    timings_ms["discover"] = round((time.perf_counter() - start) * 1000, 1)

    # Stage: assign (LLM 1b)
    start = time.perf_counter()
    assignments = assign_comments(records, theme_specs, llm)
    themes = _build_themes(theme_specs, records, assignments)
    timings_ms["assign"] = round((time.perf_counter() - start) * 1000, 1)

    # Stage: analyst (LLM 2, one call per qualifying theme — fanned out
    # concurrently; results collected in theme order so output is stable).
    start = time.perf_counter()
    analyst_jobs = []
    for theme in themes:
        if not theme_qualifies(theme, assignments):
            continue
        theme_records = [
            records_by_id[comment_id]
            for comment_id in theme.comment_ids
            if comment_id in records_by_id
        ]
        if not theme_records:
            continue
        segment_facts = compute_segments(theme_records)
        segment_facts["comment_count"] = len(theme_records)
        segment_facts["sentiment_counts"] = dict(theme.sentiment_counts)
        # 3rd analyst slot only when stage-1 independently tagged some comment
        # in this theme critical (analyst labels can't self-grant the slot).
        critical_slot = any(
            assignments.get(comment_id, {}).get("severity_signal") == "critical"
            for comment_id in theme.comment_ids
        )
        analyst_jobs.append((theme, theme_records, segment_facts, critical_slot))

    def _run_analyst(job):
        theme, theme_records, segment_facts, critical_slot = job
        try:
            return propose_work_items(
                theme, theme_records, segment_facts, llm, critical_slot=critical_slot
            )
        except Exception:
            logger.exception("v2 analyst failed for theme %s; skipping", theme.key)
            return []

    proposed: List[ProposedItem] = []
    if analyst_jobs:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(
            max_workers=min(ANALYST_CONCURRENCY, len(analyst_jobs))
        ) as pool:
            for result in pool.map(_run_analyst, analyst_jobs):
                proposed.extend(result)
    timings_ms["analyst"] = round((time.perf_counter() - start) * 1000, 1)

    # Stage: semantic merge (one LLM call; string dedup misses same-root-cause
    # items phrased differently — observed CAMS duplicate pair in run 4)
    start = time.perf_counter()
    from .merge import semantic_merge_pass

    proposed, semantic_merges = semantic_merge_pass(proposed, llm)
    timings_ms["semantic_merge"] = round((time.perf_counter() - start) * 1000, 1)

    # Stage: priority + guardrails (deterministic)
    start = time.perf_counter()
    work_items, guardrail_report = validate_and_merge(proposed, records_by_id, themes)
    guardrail_report["semantic_merges"] = semantic_merges
    timings_ms["guardrails"] = round((time.perf_counter() - start) * 1000, 1)

    timings_ms["total"] = round((time.perf_counter() - total_start) * 1000, 1)
    return PipelineResult(
        run_id=f"v2_{uuid.uuid4().hex[:12]}",
        source_summary=source_summary,
        themes=themes,
        work_items=work_items,
        guardrail_report=guardrail_report,
        llm_calls=llm.calls,
        timings_ms=timings_ms,
    )
