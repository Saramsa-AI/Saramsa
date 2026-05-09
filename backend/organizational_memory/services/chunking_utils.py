"""
Document chunking utilities for organizational memory ingestion.

Provides two public chunking strategies:
- ``chunk_by_header``: splits markdown at H2/H3 boundaries (for ADR documents)
- ``chunk_by_paragraph``: splits by paragraph with target size merging (for roadmap/PRD)
"""

import re

# Target token count per paragraph chunk (~500 tokens ≈ ~2000 characters at ~4 chars/token)
PARAGRAPH_CHUNK_TARGET_CHARS = 2000


def chunk_by_header(content: str) -> list[str]:
    """
    Split markdown content into chunks at H2 (##) and H3 (###) boundaries.

    Each chunk includes the header line and all content up to the next H2/H3
    header (or end of document). The document preamble (content before the
    first H2/H3 header) is included as its own chunk if non-empty.

    Args:
        content: Raw markdown text.

    Returns:
        List of non-empty chunk strings. Always returns at least one chunk.
    """
    # Split on lines that start with ## or ### (H2/H3)
    header_pattern = re.compile(r"^(#{2,3})\s+.+", re.MULTILINE)
    matches = list(header_pattern.finditer(content))

    if not matches:
        # No H2/H3 headers found — return the whole document as one chunk
        stripped = content.strip()
        return [stripped] if stripped else [content]

    chunks: list[str] = []

    # Preamble: content before the first header
    preamble = content[: matches[0].start()].strip()
    if preamble:
        chunks.append(preamble)

    # Each header section: from the header start to the next header start (or EOF)
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        chunk = content[start:end].strip()
        if chunk:
            chunks.append(chunk)

    return chunks if chunks else [content.strip()]


def chunk_by_paragraph(
    content: str,
    target_chars: int = PARAGRAPH_CHUNK_TARGET_CHARS,
) -> list[str]:
    """
    Split content into chunks by paragraph, merging short paragraphs to approach
    the target character count (~500 tokens).

    Paragraphs are separated by one or more blank lines. Short paragraphs are
    merged with the next until the target size is reached, then a new chunk starts.

    Args:
        content: Raw text content.
        target_chars: Target character count per chunk (default ~2000 chars ≈ 500 tokens).

    Returns:
        List of non-empty chunk strings. Always returns at least one chunk.
    """
    # Split on blank lines (one or more)
    raw_paragraphs = re.split(r"\n\s*\n", content)
    paragraphs = [p.strip() for p in raw_paragraphs if p.strip()]

    if not paragraphs:
        stripped = content.strip()
        return [stripped] if stripped else [content]

    chunks: list[str] = []
    current_parts: list[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)

        if current_len + para_len > target_chars and current_parts:
            # Flush the current chunk and start a new one
            chunks.append("\n\n".join(current_parts))
            current_parts = [para]
            current_len = para_len
        else:
            current_parts.append(para)
            current_len += para_len

    # Flush the last chunk
    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks if chunks else [content.strip()]
