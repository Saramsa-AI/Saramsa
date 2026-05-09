
# Implementation Blueprint: AI Project Management Agent (Layer 2)

## Overview
This document outlines the technical specifications for evolving the core platform from a feedback summarizer into a context-aware **AI Project Management Agent**. The goal is to move beyond simple extraction and implement a strategic layer that uses organizational memory to prioritize and enrich work items.

---

## 1. Product Memory Layer (Persistence & Embedding)
**Technology:** Neon (Serverless Postgres) + `pgvector`
**Objective:** Create a multi-modal, tenant-scoped long-term memory.

To build a multi-modal organizational memory, you should implement a partitioned vector schema within Neon. This allows you to perform hybrid searches across different types of "knowledge."

Schema Design: Use a metadata-heavy table structure.

content: The raw text (ADR, feedback, or roadmap item).

embedding: The vector representation (e.g., text-embedding-3-small).

source_type: Enum (e.g., feedback, roadmap, architecture, outcome).

tenant_id: For strict multi-tenant isolation.

### Schema Requirements
- **Table:** `organizational_memory`
- **Columns:**
    - `id`: UUID (Primary Key)
    - `tenant_id`: UUID (Indexed for multi-tenant isolation)
    - `content`: TEXT (Raw data)
    - `embedding`: VECTOR(1536) (Optimized for `text-embedding-3-small`)
    - `metadata`: JSONB (Store: `source_type`, `created_at`, `version`, `tags`)
    - `source_type`: ENUM (`feedback`, `roadmap`, `architecture_adr`, `historical_task`, `release_note`)

### Storage Strategy
- **Architecture Docs/ADRs:** Should be chunked by header to preserve context.
- **Historical Work Items:** Include the "Resolution" and "Outcome" to help the agent learn from past success/failure.

Storage Strategy: Store Architecture Decision Records (ADRs) and System Maps as high-weight nodes. This ensures that when a new feature is requested, the agent "remembers" previous technical constraints.

---

## 2. Context Retrieval Engine
**Objective:** Perform hybrid search to ground new issues in existing organizational reality.

### Search Logic
When a new issue is extracted, trigger a parallel $k$-NN search across three primary contexts:
1.  **Similarity Context:** Find similar past feedback/issues (to detect recurrence).
2.  **Strategic Context:** Pull roadmap items and PRDs (to check alignment).
3.  **Technical Context:** Pull ADRs and system maps (to assess constraints).

**Algorithm:** Use **Reciprocal Rank Fusion (RRF)** to combine the results from these three domains into a single ranked list of relevant context snippets.
The goal here is Cross-Domain Retrieval. When a new issue is extracted, the engine must perform a k-Nearest Neighbors (k-NN) search across multiple memory partitions simultaneously.

The "Grounding" Query: Instead of just searching for similar feedback, the agent queries: "What does our roadmap say about this topic, and what are the known architectural constraints?"

Ranking: Use a Reciprocal Rank Fusion (RRF) algorithm to combine results from historical feedback and strategic documentation to ensure a balanced context.
---

## 3. Issue Enrichment Layer (Signal Processing)
**Objective:** Transform raw context into structured reasoning vectors (Signals).

| Signal | Logic / Calculation |
| :--- | :--- |
| **Recurrence** | Count of similar historical clusters within the last 90 days. |
| **Urgency Trend** | Sentiment slope of incoming feedback over time. |
| **Roadmap Alignment** | Cosine similarity score between the issue and current roadmap objectives. |
| **Technical Blast Radius** | Cross-reference with System Maps to identify affected microservices/modules. |
| **Leverage** | Quantify if solving this issue resolves multiple related feedback clusters. |
This layer transforms raw text and retrieved context into quantifiable signals.

Technical Blast Radius: By retrieving System Maps/ADRs, the agent can estimate how many services a change might touch.

Urgency Trend: By looking at release_history, the agent determines if this is a regression or a persistent pain point.

Leverage Calculation: A high-leverage item is one that solves multiple "feedback clusters" stored in the memory layer with a single engineering effort.

---

## 4. Priority Scoring Engine
**Objective:** Apply a deterministic weighted scoring model (0–100).

### Scoring Formula
The core logic should follow a weighted impact vs. effort calculation:
**Score = ((Impact * Recurrence * Strategic_Fit) / Complexity) * Confidence**

### Classification Tiers
- **Critical (90-100):** Immediate action; high impact, low complexity, high alignment.
- **High (70-89):** Major pain point; requires planning in next sprint.
- **Planned (50-69):** Fits strategic direction; add to roadmap.
- **Backlog (20-49):** Valid but low leverage or high friction.
- **Defer (<20):** Out of scope or redundant.

---

## 5. Context-Aware Work Item Generator
**Objective:** Produce PM-standard artifacts for downstream tools (Jira, ADO, Asana).

### Output Fields & Requirements
- **Title:** Concise, action-oriented.
- **Why Now (Rationale):** A 2-sentence justification citing the "Recurrence" and "Roadmap Alignment" signals.
- **Engineering Context:** - Relevant ADRs to follow.
    - Potential dependencies identified in the System Map.
- **Risk Flags:** High blast radius or conflicting historical ADRs.
- **Confidence Score:** Based on the quality and quantity of retrieved context.

You can implement a weighted scoring model using a formula similar to RICE (Reach, Impact, Confidence, Effort) but augmented with Strategic Fit.

Score= 
Complexity Cost
(Impact×Recurrence×Strategic Fit)
​
 ×Confidence
Category	Score Range	Logic
Critical	90–100	High Impact + Low Complexity + Roadmap Alignment.
High	70–89	Significant Customer Pain + Moderate Technical Debt.
Planned	50–69	Aligns with Roadmap but not urgent.
Backlog	20–49	Low Impact or extremely high Complexity.
5. Context-Aware Work Item Generator
The final output must look like it was written by a Senior PM who has spoken to both the customer and the Lead Engineer.

The "Why Now" Field: This is the most critical output. It shouldn't just repeat the feedback; it should cite the Product Memory.

Example: "We are seeing a 20% spike in this feedback cluster, and our Q3 Roadmap prioritized API stability, making this a high-leverage fix."

Risk Flags: Automatically flagged if the "Technical Blast Radius" signal from Layer 3 is high or if it conflicts with an existing ADR.

The Final Integrated Workflow
Ingestion: A PDF or Slack message arrives.

Extraction: The agent identifies a "Search Latency" issue.

Memory Retrieval: It pulls a 2024 ADR on Elasticsearch and three similar tickets from last month.

Enrichment: It notes this is a recurring issue with high technical complexity.

Scoring: It receives a 78 (High) because it aligns with the current "Performance" sprint.

Dispatch: A Jira ticket is created with a pre-filled engineering estimate and a link to the relevant architecture doc.

This loop creates a self-reinforcing system where every completed work item (and its outcome) is fed back into the Product Memory, making the agent smarter with every release.


---

## Final System Flow
1. **Input:** Feedback (CSV, JSON, PDF, Slack) - already there
2. **LLM 1 (Extraction):** Identifies the core problem/intent - already there
3. **Retrieval:** Neon/pgvector fetches tenant-specific memory.
4. **LLM 2 (Enrichment):** Processes signals and calculates the Priority Score.
5. **LLM 3 (Generation):** Synthesizes all data into a formatted Work Item.
6. **Output:** Push to Integration (Jira, Slack, etc.) - already there
pm_agent_implementation.md
Displaying pm_agent_implementation.md.