"""Semantic merge pass — the LLM judges which draft items share a root cause;
code applies the merges deterministically.

String-Jaccard dedup missed real duplicates (run 4: two CAMS-import items with
title-Jaccard 0.37). One cheap LLM call closes that gap. The LLM only returns
INDEX GROUPS — never item content — so it can't alter, invent, or drop items;
code validates every group (same type, valid indexes) and performs the merge.
"""

import json
import logging
from typing import Callable, List, Tuple

from .clustering import call_llm_json
from .schemas import ProposedItem

logger = logging.getLogger(__name__)

MERGE_MAX_TOKENS = 4000

LLMCallable = Callable[[str, int], str]


def semantic_merge_pass(
    proposed: List[ProposedItem], llm: LLMCallable
) -> Tuple[List[ProposedItem], List[dict]]:
    """Merge same-root-cause draft items. Returns (items, merge_log)."""
    if len(proposed) < 2:
        return proposed, []

    lines = "\n".join(
        f"[{i}] ({item.type}) {item.title} | theme={item.theme} | evidence={','.join(item.evidence_ids)}"
        for i, item in enumerate(proposed)
    )
    prompt = f"""These are draft work items generated from customer feedback.

{lines}

Identify groups of items that describe the SAME underlying root cause — i.e. a
single engineering fix would resolve all items in the group. Do NOT group items
that merely belong to the same product area but need different fixes.

Return ONLY JSON: {{"merge_groups": [[i, j, ...], ...]}} using the [bracket]
indexes. Items not in any group are left as-is. An empty list is a valid answer."""

    try:
        parsed = call_llm_json(llm, prompt, MERGE_MAX_TOKENS)
    except Exception:
        logger.exception("v2 semantic merge call failed; keeping items unmerged")
        return proposed, []

    groups = parsed.get("merge_groups") or []
    merge_log: List[dict] = []
    consumed = set()
    merged_items = list(proposed)

    severity_rank = {"minor": 0, "moderate": 1, "major": 2, "critical": 3}

    for group in groups:
        if not isinstance(group, list) or len(group) < 2:
            continue
        try:
            indexes = sorted({int(i) for i in group})
        except (TypeError, ValueError):
            continue
        if any(i < 0 or i >= len(proposed) or i in consumed for i in indexes):
            continue
        members = [proposed[i] for i in indexes]
        # Guardrail: only merge items of the same type (a bug and a feature
        # request are never one root cause).
        if len({m.type for m in members}) != 1:
            continue

        base = max(members, key=lambda m: (len(m.evidence_ids), len(m.description or "")))
        evidence = list(dict.fromkeys(eid for m in members for eid in m.evidence_ids))
        severity = max((m.severity for m in members), key=lambda s: severity_rank.get(s, 1))
        merged = ProposedItem(
            title=base.title,
            type=base.type,
            category=base.category,
            severity=severity,
            evidence_ids=evidence,
            description=base.description,
            acceptance_criteria=base.acceptance_criteria,
            business_value=base.business_value,
            theme=base.theme,
        )
        keep_index = indexes[0]
        merged_items[keep_index] = merged
        for i in indexes[1:]:
            merged_items[i] = None
        consumed.update(indexes)
        merge_log.append(
            {
                "kept": base.title,
                "merged": [m.title for m in members if m is not base],
                "evidence_union": len(evidence),
            }
        )

    return [m for m in merged_items if m is not None], merge_log
