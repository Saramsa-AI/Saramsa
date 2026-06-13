"""LLM-as-judge evaluation of the discovery + classification pipeline.

Produces measured (proxy) numbers on a real CSV — no human labels required:
  - qualify rate            (% of items that are real feedback vs noise)
  - classification coverage (% of signal items mapped to an aspect)
  - classification accuracy (judge: is each comment's assigned aspect correct?)
  - taxonomy coherence      (judge: are the aspects distinct/clear/grounded?)

A SEPARATE LLM grades each assignment (judge != the model that produced it). This
is a proxy for human eval (typically well-correlated), not ground truth.

Run: cd backend && venv/Scripts/python.exe scripts/eval_taxonomy_pipeline.py "E:/D drive/Downloads/Book(Sheet1).csv"
"""

import os
import sys
import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

os.environ.setdefault("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-ada-002")
os.environ["DISCOVERY_QUALIFY"] = "off"  # we qualify once manually below, then discover on the signal set

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from aiCore.services.openai_client import get_azure_client, get_azure_deployment_name
from aiCore.services.feedback_extraction_service import get_feedback_extraction_service
from aiCore.services.taxonomy_discovery_service import get_taxonomy_discovery_service
from aiCore.services.llm_aspect_service import get_llm_aspect_service


def load_comments(path):
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        rows = list(csv.DictReader(f))
    best = max(rows[0].keys(), key=lambda c: sum(len(str(r.get(c) or "")) for r in rows) / max(1, len(rows)))
    cs = [str(r.get(best) or "").strip() for r in rows]
    return [c for c in cs if c and c.lower() != "nan"], best


def _judge_call(client, system, user):
    call = client.with_options(timeout=60, max_retries=2)
    resp = call.chat.completions.create(
        model=get_azure_deployment_name(),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_completion_tokens=500, response_format={"type": "json_object"}, reasoning_effort="low",
    )
    return json.loads(resp.choices[0].message.content or "{}")


def judge_assignments(client, texts, results, aspects):
    """One judge call per comment: is the assigned aspect correct for this comment?"""
    taxonomy = ", ".join(aspects)
    system = (
        "You are evaluating an aspect-classification system. Given a customer comment, the "
        "aspect(s) the system assigned, and the full aspect list, judge whether the assignment "
        "is correct (the assigned aspect is the best/a valid fit for what the comment is about). "
        'Respond JSON: {"verdict": "correct" | "partial" | "wrong"}. '
        "Use 'partial' if an assigned aspect is related but not the best, or a better one exists."
    )
    verdicts = [None] * len(texts)

    def one(i):
        assigned = results[i]["matched_aspects"]
        user = f"Comment: {texts[i][:600]}\nAssigned: {assigned}\nAspect list: {taxonomy}"
        try:
            v = _judge_call(client, system, user).get("verdict", "wrong")
        except Exception:
            v = "error"
        return i, v

    with ThreadPoolExecutor(max_workers=20) as pool:
        futs = [pool.submit(one, i) for i in range(len(texts))]
        for f in as_completed(futs):
            i, v = f.result()
            verdicts[i] = v
    return verdicts


def judge_taxonomy(client, domain, aspects):
    system = (
        "Rate this auto-discovered aspect taxonomy for a customer-feedback dataset on three "
        "axes, each 0-100: distinctness (aspects non-redundant), clarity (well-defined, specific, "
        "not generic), grounded (plausibly real themes for the domain). "
        'Respond JSON: {"distinctness": int, "clarity": int, "grounded": int, "notes": "..."}.'
    )
    user = f"Domain: {domain}\nAspects: {aspects}"
    try:
        return _judge_call(client, system, user)
    except Exception as e:
        return {"error": str(e)[:100]}


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else r"E:/D drive/Downloads/Book(Sheet1).csv"
    comments, col = load_comments(path)
    client = get_azure_client().get_client()
    print(f"loaded {len(comments)} comments from column {col!r}")
    t0 = time.time()

    # 1. Qualify (signal vs noise)
    qualified = get_feedback_extraction_service().qualify(comments)
    signal = [q for q in qualified if q["has_signal"]]
    signal_texts = [q["core_content"] for q in signal]
    qualify_rate = len(signal) / len(comments)
    print(f"[qualify] signal={len(signal)} noise={len(comments)-len(signal)} ({qualify_rate:.0%} signal)")

    # 2. Discover taxonomy on the signal set (qualify already done -> DISCOVERY_QUALIFY=off)
    disc = get_taxonomy_discovery_service().discover(signal_texts)
    aspects = disc["suggested_aspects"]
    print(f"[discover] domain={disc['identified_domain']!r} aspects={len(aspects)} clusters={disc.get('n_clusters')}")

    # 3. Classify the signal comments against the taxonomy
    results = get_llm_aspect_service().classify_aspects(signal_texts, aspects)
    mapped = [r for r in results if r["matched_aspects"] and r["matched_aspects"] != ["UNMAPPED"]]
    coverage = len(mapped) / len(results)
    print(f"[classify] coverage={coverage:.0%} ({len(mapped)}/{len(results)} mapped)")

    # 4. Judge each mapped assignment
    judged_idx = [i for i, r in enumerate(results) if r in mapped]
    verdicts = judge_assignments(client, [signal_texts[i] for i in judged_idx],
                                 [results[i] for i in judged_idx], aspects)
    counts = {"correct": 0, "partial": 0, "wrong": 0, "error": 0}
    for v in verdicts:
        counts[v] = counts.get(v, 0) + 1
    n = sum(counts.values()) or 1
    strict = counts["correct"] / n
    lenient = (counts["correct"] + counts["partial"]) / n  # partial counts as half elsewhere
    half = (counts["correct"] + 0.5 * counts["partial"]) / n

    # 5. Taxonomy coherence
    coh = judge_taxonomy(client, disc["identified_domain"], aspects)

    dt = time.time() - t0
    print("\n" + "=" * 60)
    print(f"PIPELINE EVAL  ({dt:.0f}s, judge=LLM-as-judge proxy)")
    print("=" * 60)
    print(f"qualify rate (real feedback):     {qualify_rate:.0%}  ({len(signal)}/{len(comments)})")
    print(f"classification coverage:          {coverage:.0%}  ({len(mapped)}/{len(results)})")
    print(f"assignment verdicts:              {counts}")
    print(f"classification accuracy (strict): {strict:.0%}  (correct only)")
    print(f"classification accuracy (half):   {half:.0%}  (partial=0.5)")
    print(f"classification accuracy (lenient):{lenient:.0%}  (correct+partial)")
    print(f"taxonomy coherence (0-100):       {coh}")
    # composite: signal-weighted end-to-end correctness on the whole upload
    e2e = qualify_rate * coverage * half
    print(f"end-to-end (signal x coverage x half-acc): {e2e:.0%}")


if __name__ == "__main__":
    main()
