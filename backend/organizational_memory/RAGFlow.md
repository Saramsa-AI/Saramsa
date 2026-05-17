Let me read the actual code files to trace the exact flow.

Here's the exact code flow, traced through the actual files:

---

## Exact Code Flow

### Entry point: `feedback_analysis/tasks.py`
A Celery worker picks up the task and calls:
```python
task_service.process_feedback_background(comments, ..., project_id, analysis_id)
```

---

### `task_service.py` → `process_feedback_background()`

```
1. Runs LLM 1 (extraction)
   └── _process_with_llm_chunking()  OR  _process_with_local_pipeline()
       → returns result dict with extracted features/issues

2. Calls self._run_rag_enrichment(result, user_id_str, project_id, analysis_id)
   └── wrapped in try/except — RAG failure NEVER aborts the main pipeline
```

---

### `task_service.py` → `_run_rag_enrichment()`

```
1. Fetches Project from DB
   └── checks project.metadata["rag_enabled"]
   └── if False → returns {} immediately (RAG skipped)

2. Derives tenant_id = str(project.user_id)  ← always server-side, never from client

3. Builds extracted_issues list from the LLM 1 result

4. Calls run_rag_pipeline(extracted_issues, project_id, user_id, tenant_id)
   └── on ConnectionError (Azure down) → falls back, sets extra["rag_enabled"] = False
   └── on success → calls _save_rag_candidates_to_analysis()
```

---

### `pipeline_integration.py` → `run_rag_pipeline()`

Loops over each extracted issue and calls `_enrich_single_issue()`:

```
For each issue:
  │
  ├── Step 1: _retrieve_with_retry()
  │     └── ContextRetrievalEngine.retrieve_context(query_text, tenant_id, k=5)
  │           ├── Embeds query once (Azure OpenAI text-embedding-3-small)
  │           ├── 3x parallel k-NN searches via asyncio.gather:
  │           │     ├── feedback domain    → past similar complaints
  │           │     ├── roadmap domain     → strategic alignment
  │           │     └── architecture_adr  → technical constraints
  │           └── RRF fusion → single ranked list
  │           (retries up to 3x with 2s/4s/8s backoff on ConnectionError)
  │
  ├── Step 2: IssueEnrichmentService.compute_signals(issue, fused_context, tenant_id)
  │     └── recurrence, urgency_trend, roadmap_alignment, blast_radius, leverage, confidence
  │
  ├── Step 3: PriorityScoreEngine.compute_score(signals)
  │     └── Score = ((Impact × Recurrence × Strategic_Fit) / Complexity) × Confidence × 100
  │     └── Maps to tier: critical / high / planned / backlog / defer
  │
  ├── Step 4: IssueEnrichmentService.enrich_and_generate()  ← async, run via _run_async()
  │     ├── LLM 2 call → why_now, risk_flags, confidence_score
  │     └── LLM 3 call → title, description, engineering_context, risk_flags
  │
  └── Step 5: Builds enriched dict with extra = {
          rag_enabled: True,
          priority_score, priority_tier, confidence_score,
          why_now, engineering_context, risk_flags,
          signals: { recurrence, urgency_trend, roadmap_alignment, ... },
          retrieval_provenance: [...]
      }
```

---

### After the pipeline

Back in `_run_rag_enrichment()`:
```python
self._save_rag_candidates_to_analysis(insight_id, enriched_candidates)
```
The enriched candidates are saved to the analysis record so `DevOpsService.generate_work_items_from_analysis()` picks them up and creates `WorkItemCandidate` rows with the full RAG metadata in `extra`.

---

### Later: memory grows automatically - tbd

When a `WorkItemCandidate` status changes to `done` or `closed`, the post-save signal in `organizational_memory/signals.py` fires and auto-ingests it back as a `historical_task` — so future feedback runs have more context to work with.