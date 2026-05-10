"""
Sentiment Analysis Prompts

Centralized sentiment analysis prompts. This module provides direct access to 
sentiment analysis prompts without depending on the aiCore prompt system to avoid circular imports.
"""

import logging

from .constants import get_prompt
from .resolver import resolve_prompt_template

logger = logging.getLogger(__name__)


def format_comments_for_prompt(comments, start_index: int = 0):
    """
    Format comments with clear indexes for LLM processing.

    This ensures LLM can correctly map outputs back to input comments.
    Uses global comment indices across batches so comment_id matches the original comment position.

    Args:
        comments: List of (index, text) tuples, list of comment strings, or single string with newline-separated comments
        start_index: Starting index for this batch (used when comments is a plain list or string)

    Returns:
        Formatted string with "COMMENT {index}:" prefixes
    """
    # Handle list of (index, text) tuples from token-based batching
    if isinstance(comments, list) and comments and isinstance(comments[0], (tuple, list)) and len(comments[0]) == 2:
        formatted_lines = []
        for global_index, comment_text in comments:
            text = str(comment_text).strip()
            if text:
                formatted_lines.append(f"COMMENT {global_index}: {text}")
        return "\n".join(formatted_lines)

    # Handle list of comment strings
    if isinstance(comments, list):
        formatted_lines = []
        for i, comment in enumerate(comments):
            comment_text = str(comment).strip()
            if comment_text:
                global_index = start_index + i
                formatted_lines.append(f"COMMENT {global_index}: {comment_text}")
        return "\n".join(formatted_lines)

    # Handle string (newline-separated comments)
    if isinstance(comments, str):
        lines = comments.split('\n')
        formatted_lines = []
        for i, line in enumerate(lines):
            line_text = line.strip()
            if line_text:
                global_index = start_index + i
                formatted_lines.append(f"COMMENT {global_index}: {line_text}")
        return "\n".join(formatted_lines)

    return str(comments)


def getSentAnalysisPrompt(company_name: str = None, feedback_data: str = None,
                         industry: str = None, use_confidence: bool = False,
                         suggested_aspects: list = None, comment_start_index: int = 0,
                         project_id: str = None, organization_id: str = None):
    """
    Get sentiment analysis prompt using the enhanced centralized prompt system.
    
    Args:
        company_name: Optional company name to get company-specific prompt
        feedback_data: Feedback comments/data to inject into prompt (can be list, string, or formatted string)
        industry: Optional industry context ('saas', 'ecommerce', 'mobile_app')
        use_confidence: Whether to use confidence-aware sentiment analysis
        suggested_aspects: Optional list of approved aspects (frozen aspect list) - LLM MUST use only these
    
    Returns:
        Rendered sentiment analysis prompt string with variables substituted
    
    Example:
        prompt = getSentAnalysisPrompt(
            company_name="Acme Corp",
            feedback_data=["Comment 1", "Comment 2"],  # or string
            industry="saas",
            use_confidence=True,
            suggested_aspects=["Product Quality", "Customer Service", "User Experience"]
        )
    """
    # Get the enhanced prompt template
    prompt_template = get_prompt(
        company_name=company_name,
        prompt_type="sentiment",
        industry=industry,
        use_confidence=use_confidence
    )

    # If a superadmin has saved a tenant- or platform-wide override for
    # this prompt type, swap in their version before variable substitution.
    override_type = "sentiment_confidence" if use_confidence else "sentiment"
    prompt_template = resolve_prompt_template(
        override_type,
        prompt_template,
        project_id=project_id,
        organization_id=organization_id,
    )

    # Log original template before substitution
    logger.debug("Sentiment prompt template (before substitution): %s", prompt_template[:200] + "...")

    # Format aspect list for {aspects} placeholder
    if suggested_aspects and len(suggested_aspects) > 0:
        # Format as simple list, one per line
        aspect_list_text = "\n".join([f"- {aspect}" for aspect in suggested_aspects])
    else:
        aspect_list_text = "(No aspects provided - use empty array [] for all comments)"

    # Format comments for {comments} placeholder with global indices
    if feedback_data:
        # Format comments with global indexes for better LLM parsing
        # comment_start_index ensures correct mapping across batches
        formatted_comments = format_comments_for_prompt(feedback_data, start_index=comment_start_index)
        logger.info(f"🔍 Formatted {len(formatted_comments.split('COMMENT')) - 1} comments with start_index={comment_start_index}")
        logger.debug(f"🔍 First 3 formatted comments:\n{chr(10).join(formatted_comments.split(chr(10))[:3])}")
    else:
        formatted_comments = "(No comments provided)"
        logger.warning("⚠️ No feedback_data provided to prompt")

    # Replace placeholders with actual values
    prompt_template = prompt_template.replace("{aspects}", aspect_list_text)
    prompt_template = prompt_template.replace("{comments}", formatted_comments)

    # Log final template after substitution
    logger.info(f"🔍 Prompt template ready - Length: {len(prompt_template)} chars, Aspects: {len(suggested_aspects) if suggested_aspects else 0}")
    logger.debug(f"🔍 Prompt preview (first 500 chars):\n{prompt_template[:500]}...")

    return prompt_template
