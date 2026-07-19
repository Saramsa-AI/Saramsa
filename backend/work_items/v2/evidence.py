"""CSV/JSON rows -> EvidenceRecord list, with robust column detection.

Pure stdlib. Callers hand us already-parsed row dicts (csv.DictReader with
utf-8-sig, or a JSON "rows" array); we additionally strip any BOM that leaked
into column names and stringify every metadata value.
"""

import re
from typing import Dict, List, Optional, Tuple

from .schemas import EvidenceRecord

_TEXT_COLUMN_RE = re.compile(
    r"^(feedback_text|feedback|comment|text|review|message)$", re.IGNORECASE
)
_ID_COLUMN_RE = re.compile(r"^(feedback_id|id|ticket_id|row_id)$", re.IGNORECASE)

# Preference order within the name-matched candidates (most specific first).
_TEXT_NAME_PRIORITY = ["feedback_text", "feedback", "comment", "text", "review", "message"]
_ID_NAME_PRIORITY = ["feedback_id", "id", "ticket_id", "row_id"]


def _clean_key(key: object) -> str:
    return str(key).lstrip("﻿").strip() if key is not None else ""


def _normalize_rows(rows: List[Dict]) -> List[Dict[str, object]]:
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        clean = {}
        for key, value in row.items():
            ck = _clean_key(key)
            if ck:
                clean[ck] = value
        normalized.append(clean)
    return normalized


def _pick_by_priority(columns: List[str], priority: List[str]) -> Optional[str]:
    lowered = {c.lower(): c for c in columns}
    for name in priority:
        if name in lowered:
            return lowered[name]
    return None


def _detect_text_column(columns: List[str], rows: List[Dict[str, object]]) -> Optional[str]:
    named = _pick_by_priority(
        [c for c in columns if _TEXT_COLUMN_RE.match(c)], _TEXT_NAME_PRIORITY
    )
    if named:
        return named
    # Fallback: column with highest mean string length.
    best, best_mean = None, -1.0
    for column in columns:
        lengths = [
            len(str(row.get(column) or "")) for row in rows if column in row
        ]
        mean = sum(lengths) / len(lengths) if lengths else 0.0
        if mean > best_mean:
            best, best_mean = column, mean
    return best


def _detect_id_column(columns: List[str]) -> Optional[str]:
    return _pick_by_priority(
        [c for c in columns if _ID_COLUMN_RE.match(c)], _ID_NAME_PRIORITY
    )


def parse_rows(rows: List[Dict[str, str]]) -> Tuple[List[EvidenceRecord], Dict]:
    """Parse raw row dicts into EvidenceRecords + a source summary.

    - text column: prefer name match, else highest mean string length.
    - id column: name match, else synthesize R0001...; duplicate ids get -2, -3...
    - all other columns -> metadata (values stringified).
    - empty-text rows skipped (counted in the summary).
    """
    normalized = _normalize_rows(rows or [])
    columns: List[str] = []
    for row in normalized:
        for key in row.keys():
            if key not in columns:
                columns.append(key)

    text_column = _detect_text_column(columns, normalized) if normalized else None
    id_column = _detect_id_column(columns) if normalized else None
    metadata_columns = [
        c for c in columns if c != text_column and c != id_column
    ]

    records: List[EvidenceRecord] = []
    seen_ids: Dict[str, int] = {}
    skipped_empty_text = 0

    for index, row in enumerate(normalized):
        text = str(row.get(text_column) or "").strip() if text_column else ""
        if not text:
            skipped_empty_text += 1
            continue

        raw_id = str(row.get(id_column) or "").strip() if id_column else ""
        record_id = raw_id or f"R{index + 1:04d}"
        if record_id in seen_ids:
            seen_ids[record_id] += 1
            record_id = f"{record_id}-{seen_ids[record_id]}"
        seen_ids.setdefault(record_id, 1)

        metadata = {
            column: "" if row.get(column) is None else str(row.get(column))
            for column in metadata_columns
            if column in row
        }
        records.append(EvidenceRecord(id=record_id, text=text, metadata=metadata))

    summary = {
        "rows": len(normalized),
        "parsed_rows": len(records),
        "skipped_empty_text": skipped_empty_text,
        "text_column": text_column,
        "id_column": id_column,
        "metadata_columns": metadata_columns,
    }
    return records, summary
