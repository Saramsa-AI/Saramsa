Here's the full sequence based on the codebase:

## 1. Onboarding a User / Setting Up RAG

Before RAG does anything useful, the tenant needs organizational memory seeded. This is a one-time setup per project.

**Step 1 — Enable RAG on the project**

Set `rag_enabled: true` in the project's metadata field. Without this flag, the RAG pipeline is skipped entirely and the system falls back to standard work item generation.

**Step 2 — Ingest organizational documents**

The user (or admin) calls `POST /api/memory/ingest/` for each document they want the system to learn from:

- ADRs (Architecture Decision Records) → `source_type: "architecture_adr"` — chunked by markdown H2/H3 headers
- Roadmap / PRD documents → `source_type: "roadmap"` — chunked by paragraph (~500 tokens)
- Release notes → `source_type: "release_note"`

Each chunk gets embedded via Azure OpenAI `text-embedding-3-small` and stored in the `organizational_memory` table with the tenant's ID.

At this point the memory store is seeded and ready.

---

## 2. User Uploads a CSV of Feedback

Here's what happens end to end:

**Step 1 — CSV ingestion**

The CSV is uploaded through the existing feedback ingestion flow. The feedback items are parsed and queued as a Celery task (`process_feedback_background`).

**Step 2 — LLM 1: Extraction (existing)**

The existing pipeline runs first. LLM 1 reads the raw feedback and extracts structured issues — each with a `title`, `intent`, and `aspect_key`.

**Step 3 — RAG kicks in (new)**

For each extracted issue, if `rag_enabled=True` on the project:

- The issue title + description is embedded once (one Azure API call)
- Three parallel k-NN searches run against `organizational_memory`:
  - **Similarity domain** → searches `feedback` chunks (past similar complaints)
  - **Strategic domain** → searches `roadmap` chunks (what's planned)
  - **Technical domain** → searches `architecture_adr` chunks (system constraints)
- Results are fused via Reciprocal Rank Fusion into a single ranked list

**Step 4 — Signal computation**

From the retrieved context, five signals are computed:
- **Recurrence** — how many similar feedback clusters appeared in the last 90 days
- **Urgency trend** — is sentiment getting worse over time (linear regression slope)
- **Roadmap alignment** — how closely this issue matches current roadmap priorities
- **Blast radius** — which services/modules would be affected (extracted from ADR chunks)
- **Leverage** — how many other feedback clusters this fix would resolve

**Step 5 — LLM 2: Enrichment**

A second LLM call takes the issue + context + signals and produces:
- `why_now` — a rationale citing recurrence and roadmap alignment
- `risk_flags` — e.g. "High blast radius: auth-service, api-gateway"
- `confidence_score`

**Step 6 — LLM 3: Generation**

A third LLM call synthesizes everything into a fully formed work item:
- `title`, `description`, `why_now`, `engineering_context`, `risk_flags`

**Step 7 — WorkItemCandidate saved**

The work item is saved with full RAG metadata in `extra`:
```json
{
  "rag_enabled": true,
  "priority_score": 87.3,
  "priority_tier": "high",
  "confidence_score": 0.82,
  "why_now": "...",
  "engineering_context": "...",
  "risk_flags": [...],
  "signals": { "recurrence": 4, "roadmap_alignment": 0.71, ... }
}
```

**Step 8 — Memory grows over time**

When a work item is marked `done` or `closed`, a post-save signal automatically ingests it back into `organizational_memory` as a `historical_task`. So the next time similar feedback arrives, the system knows this was already resolved and how.

---

**If RAG fails at any point** (embedding API down, etc.) — after 3 retries it falls back gracefully to the non-RAG pipeline. The work item is still generated, just without the enrichment signals, and `extra["rag_enabled"]` is set to `false`.