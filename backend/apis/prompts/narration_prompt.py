"""Unified narration prompt for Phase-3.

This single prompt covers analysis narratives (insights + feature descriptions)
and rich work-item narratives (from deterministic candidates + sampled comments).

The default template can be overridden per-tenant or platform-wide via the
`work_items_validation` prompt type. Overrides MUST keep the literal token
``{narration_input}`` somewhere in the body — at render time it's replaced
with the JSON payload (overall sentiment, features, work_item_candidates,
comment_samples) that the LLM needs to produce work items.
"""

import json
from typing import Any, Dict, Optional

from .resolver import resolve_prompt_template


_DEFAULT_TEMPLATE = """You are a senior product analyst creating actionable work items from customer feedback.

## YOUR TASK
For each work_item_candidate in the input, generate a rich, specific work item using the analysis metrics AND the sampled customer comments provided.

## INPUT
The JSON below contains:
- "overall": overall sentiment breakdown
- "features": feature-level sentiment data with keywords
- "work_item_candidates": deterministic candidates (each has candidate_id, aspect_key, type, priority, reason)
- "comment_samples": actual customer feedback quotes mapped by candidate_id

{narration_input}

## OUTPUT — VALID JSON WITH THIS EXACT SCHEMA

{{
  "insights": [
    "max 5 high-level observations about the feedback trends"
  ],
  "features": [
    {{
      "aspect_key": "pricing",
      "description": "1-2 sentence summary of what customers are saying about this feature"
    }}
  ],
  "work_items": [
    {{
      "candidate_id": "must match a candidate_id from the input",
      "title": "Specific, actionable title (max 100 chars). Reference the actual problem, not generic 'Improve X'",
      "description": "2-4 sentences synthesizing the key themes from customer comments. Quote specific customer phrases when available. End with a recommended action.",
      "acceptance_criteria": "3-5 measurable bullet points separated by ' | ' that define when this work item is done",
      "business_value": "Impact statement using the actual metrics from the input (comment count, negative %, proportion of total feedback)"
    }}
  ]
}}

## STRICT RULES
1. **CRITICAL**: Output one work_item for EACH candidate_id in the input. Do NOT add or remove candidates.
2. **CRITICAL**: Use the EXACT candidate_id value from each work_item_candidate (e.g., "a1b2c3d4-e5f6-7890-abcd-ef1234567890"). Copy it character-for-character. Do NOT generate new IDs or modify existing ones.
3. Do NOT change type, priority, or any metric values - you only write narrative text (title, description, acceptance_criteria, business_value).
4. Do NOT include priority, type, or metric numbers in the title.
5. For "title": Be specific. BAD: "Improve Pricing based on feedback". GOOD: "Restructure pricing tiers to address cost concerns".
6. For "description": Reference actual customer language from comment_samples. Group feedback into 2-3 themes. End with a concrete recommendation.
7. For "acceptance_criteria": Write measurable outcomes, not vague goals. BAD: "Fix the issue". GOOD: "Billing page shows itemized breakdown before charge".
8. For "business_value": Always reference the actual numbers (e.g. "350 comments, 55% negative, largest feedback category").
9. insights: max 5 items.
10. features: only for aspect_key values present in the input.
11. If comment_samples is empty for a candidate, still generate rich text based on the aspect_key, keywords, and metrics available.
12. For candidates with type "strength": write a title highlighting what customers love (e.g. "Preserve fast one-click checkout experience"), a description of what makes it successful with customer quotes, acceptance criteria focused on maintaining/amplifying the strength, and business value emphasizing retention/differentiation.
13. Return ONLY valid JSON. No explanation or commentary outside the JSON.
"""


def create_narration_prompt(
    narration_input: Dict[str, Any],
    *,
    project_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> str:
    """Create the narration prompt with a fixed JSON output schema."""
    serialized = json.dumps(narration_input, ensure_ascii=True)

    if project_id is None:
        project_id = narration_input.get("project_id") if isinstance(narration_input, dict) else None

    template = resolve_prompt_template(
        "work_items_validation",
        _DEFAULT_TEMPLATE,
        project_id=project_id,
        organization_id=organization_id,
    )
    return template.replace("{narration_input}", serialized)
