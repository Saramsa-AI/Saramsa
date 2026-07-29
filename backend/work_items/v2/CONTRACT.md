# Work Items V2 — Experimental Generation Pipeline (Contract)

**Status:** experimental. Lives entirely in `backend/work_items/v2/`. The ONLY change
outside this package is one additive URL route. The existing generation flow is untouched.

## Design principle (from the 2026-07-19 Tickertape audit)

> LLM decides *what* and *how bad* (reading real evidence); deterministic code decides
> *how urgent* (explainable math); guardrails ensure it's *real* (citation-only evidence).

Root causes this pipeline fixes vs V1:
1. **No stale taxonomy**: themes are discovered from the uploaded content itself (LLM), never
   from a stored per-project taxonomy. (V1 ran a hotel taxonomy on fintech data → 83.5% unmapped.)
2. **Metadata preserved**: every non-text CSV column rides along as structured metadata and
   feeds segment analysis + priority weighting. (V1 stripped persona/plan/rating.)
3. **Content severity**: the LLM tags severity (data-integrity / money-movement / crash / billing)
   per item, so a single "revenue wrong by 20%" comment can reach critical. (V1 was volume-only.)
4. **Anti-hallucination**: the LLM outputs only evidence **ids**; all quotes/segments are rendered
   by code from the source rows. An item with no valid citations is dropped.

## Module layout

```
backend/work_items/v2/
├── __init__.py
├── CONTRACT.md          (this file)
├── schemas.py           dataclasses + to_dict; NO Django imports
├── evidence.py          parse_rows() — CSV/JSON rows → EvidenceRecord list
├── clustering.py        discover_themes(), assign_comments() — LLM stages 1a/1b
├── analyst.py           propose_work_items() — LLM stage 2 (citation-only)
├── priority.py          score_item() — deterministic, explainable
├── guardrails.py        validate_and_merge() — citations, vocab, dedup, stable ids
├── pipeline.py          run_v2_pipeline() — sync orchestrator
└── views.py             WorkItemV2PreviewView (POST, preview-only, no persistence)
```

## schemas.py

```python
@dataclass
class EvidenceRecord:
    id: str                      # from id-like column (feedback_id/id) else "R{row_index:04d}"
    text: str                    # the feedback text column
    metadata: Dict[str, str]     # ALL other columns verbatim (persona, plan, rating, ...)

@dataclass
class Theme:
    key: str                     # snake_case stable key, e.g. "data_quality"
    label: str                   # display label
    description: str
    comment_ids: List[str]
    sentiment_counts: Dict[str, int]   # {"negative": n, "positive": n, "mixed": n, "unknown": n}

@dataclass
class PriorityBreakdown:
    severity: str                # from LLM: critical|major|moderate|minor
    severity_weight: float
    n_evidence: int
    volume_factor: float
    paid_share: float            # fraction of evidence rows on paying plans (metadata-derived)
    low_rating_share: float      # fraction with rating <= 2 (when rating column exists)
    segment_multiplier: float
    score: float
    floors_caps_applied: List[str]
    explanation: str             # one human sentence

@dataclass
class WorkItemV2:
    id: str                      # "wi_" + sha1(f"{theme}|{type}|{','.join(sorted(evidence_ids))}")[:12]
    title: str
    type: str                    # bug | improvement | feature_request | strength
    category: str                # data_integrity | money_movement | billing | crash | performance |
                                 # reliability | ux | pricing | support | feature_gap | other
    severity: str                # critical | major | moderate | minor   (LLM, content-based)
    priority: str                # critical | high | medium | low        (DETERMINISTIC ONLY)
    priority_breakdown: PriorityBreakdown
    theme: str                   # Theme.key
    evidence_ids: List[str]      # validated citations
    evidence: List[Dict]         # rendered BY CODE from source: {id, quote(<=200ch), metadata}
    affected_segments: Dict      # computed BY CODE: {"plans": {...}, "personas": {...}, "platforms": {...}, "avg_rating": x}
    description: str             # LLM
    acceptance_criteria: str     # LLM — must be testable, no invented numeric SLAs
    business_value: str          # LLM — must reference real segment facts provided to it

@dataclass
class PipelineResult:
    run_id: str
    source_summary: Dict         # {"rows": n, "text_column": ..., "id_column": ..., "metadata_columns": [...]}
    themes: List[Theme]
    work_items: List[WorkItemV2]
    guardrail_report: Dict       # {"dropped_no_citation": n, "invalid_ids_stripped": n, "merged_pairs": [...], "vocab_fixes": n}
    llm_calls: int
    timings_ms: Dict[str, float]
```

## evidence.py

`parse_rows(rows: List[Dict[str, str]]) -> Tuple[List[EvidenceRecord], Dict]`
- text column: prefer names matching `(feedback_text|feedback|comment|text|review|message)` (case-insens.);
  fallback = column with highest mean string length.
- id column: `(feedback_id|id|ticket_id|row_id)`; fallback synthesize `R0001…`. Duplicate ids → suffix `-2`.
- Everything else → metadata (values stringified). Empty text rows are skipped (counted in summary).

## clustering.py  (LLM stage 1 — replaces stored taxonomy)

- `discover_themes(records, company_name, llm) -> List[ThemeSpec]`
  One call. Input: ALL texts truncated to 240 chars each, with ids. Ask for 6–15 themes
  `{key, label, description}` grounded in this content + an `identified_domain` string.
  (200×240ch ≈ 12k tokens — fine. For >500 rows: sample 400 evenly for discovery.)
- `assign_comments(records, themes, llm) -> assignments`
  Batches of 60. For each comment id: `{themes: [keys], sentiment: negative|positive|mixed,
  severity_signal: none|moderate|major|critical, category: <category vocab>}`.
  Unknown theme keys from the model → dropped. A comment may map to up to 2 themes;
  none → theme "__unthemed__" (kept, reported, NOT silently lost — this replaces V1's UNMAPPED black hole).
- JSON handling: strip ``` fences; one retry on parse failure with "Return ONLY valid JSON".

## analyst.py  (LLM stage 2)

`propose_work_items(theme, theme_records, segment_facts, llm) -> List[ProposedItem]`
- One call per theme (themes with <2 comments AND no major/critical severity_signal are skipped).
- Input: the theme's full comment texts + ids, plus CODE-COMPUTED segment_facts
  (counts by plan/persona/platform, avg rating, sentiment counts) so business_value can cite REAL numbers.
- Output per item (0–3 per theme; strengths max 1, only if ≥60% positive AND ≥3 comments):
  `{title, type, category, severity, evidence_ids, description, acceptance_criteria, business_value}`
- HARD RULES in prompt: evidence_ids only from the provided ids; no invented metrics/SLAs;
  severity reflects CONTENT (money stuck / wrong data / crash ⇒ critical even if 1 comment);
  split distinct root causes into separate items; don't pad.

## priority.py  (DETERMINISTIC — the LLM never sets priority)

```
severity_weight: critical=4.0, major=3.0, moderate=2.0, minor=1.0
volume_factor   = 1 + log2(1 + n_evidence)
paid_share      = paying-plan evidence / n_evidence      (plan column matching pro|paid|premium|plus|enterprise; else 0)
low_rating_share= evidence with rating<=2 / n_evidence   (when rating parseable; else 0)
segment_multiplier = 1 + 0.5*paid_share + 0.3*low_rating_share
score = severity_weight * volume_factor * segment_multiplier

Floors/caps (applied after scoring, recorded in floors_caps_applied):
  F1: severity=critical AND category in {data_integrity, money_movement, billing, crash, security}
      → priority ≥ high; AND (n_evidence ≥ 2 OR paid_share > 0) → critical
  C1: n_evidence == 1 AND severity in {moderate, minor} → cap at medium
  C2: type == strength → priority = low (informational)

Bands: score ≥ 12 → critical; ≥ 7 → high; ≥ 3.5 → medium; else low
```

## guardrails.py

`validate_and_merge(proposed, records_by_id, themes) -> (List[WorkItemV2], report)`
1. Citation validation: strip ids not in `records_by_id`; item left with 0 → DROP (counted).
2. Vocab enforcement: type/category/severity outside vocab → nearest fallback
   (type→improvement, category→other, severity→moderate), counted as vocab_fixes.
3. Evidence + segments rendered by code (never from LLM text).
4. Dedup/merge: same (theme, type) with evidence-Jaccard ≥ 0.5 OR title token-Jaccard ≥ 0.6
   → merge (union evidence, keep higher severity, keep longer description), re-score, log pair.
5. Stable id assigned last. Cap output at 25 items (drop lowest score; log).

## pipeline.py

`run_v2_pipeline(rows, company_name="Company", user_id=None, project_id=None, options=None) -> PipelineResult`
- Sync entrypoint (uses async_to_sync internally, mirroring narration_service pattern).
- LLM via `aiCore.services.completion_service.generate_completions`
  (`task_type="v2_workitems_experiment"`); returns `(content, usage)`.
- Stage order: parse → discover → assign → per-theme analyst → priority → guardrails.
- MUST be import-safe without Django settings for schemas/priority/guardrails (pure logic),
  so unit tests can run them without the app.

## views.py + URL (the ONLY touch outside v2/)

- `WorkItemV2PreviewView(APIView)` — `permission_classes = [IsAuthenticated]`.
- `POST /api/work-items/v2/generate-preview/`
  - multipart with `file` (.csv) OR JSON body `{"rows": [...], "company_name": "..."}`
  - CSV parsed with `csv.DictReader` (utf-8-sig).
  - Returns `StandardResponse.success(data=PipelineResult.to_dict())`. **No persistence.**
- In `work_items/urls.py` add ONE line: `path('v2/generate-preview/', WorkItemV2PreviewView.as_view(), name='work_items_v2_preview')` (import from `.v2.views`).

## Evaluation (separate — scratchpad, not shipped in repo)

Eval harness lives in scratchpad `v2_eval/`:
- `fixtures/ideal_backlog.json` — 18 ground-truth items w/ supporting feedback_ids + min-priority
- `evaluate.py` — deterministic: match pipeline items→ideal via evidence-id overlap (≥1 shared id,
  best overlap wins), report coverage of 18, top-5 severest capture, citation validity vs CSV,
  duplicate detection, priority sanity vs fixture minimums, golden checks:
  FB002 (data accuracy) in ≥high item; FB142+FB154 (money movement) ≥high; FB128 (XIRR) present;
  FB020/FB041 (trial billing) ≥high; FB054 (crash) present; segments: a pricing item's business_value
  or affected_segments must reflect Free-plan dominance.
- Output: `v2_eval/report.md` + exit code (0 pass / 1 fail) + JSON metrics.
```
