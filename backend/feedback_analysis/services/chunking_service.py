"""
Feedback Chunking Service

Handles intelligent text chunking for different types of AI analysis.
Uses token-based batching: batches are formed by max input tokens per API call.
Comments are never split; if adding a comment would exceed the limit, it goes to the next batch.

Moved from aiCore/chunker.py to follow proper Django app architecture.
Now properly organized in the services folder.
"""

import os
import re
import logging
from typing import List, Dict, Any

import tiktoken

from aiCore.services.openai_client import get_azure_deployment_name

logger = logging.getLogger(__name__)

# Default max input tokens per batch (128K context minus prompt/output reserve).
# Overridable via MAX_INPUT_TOKENS_PER_BATCH env.
_DEFAULT_MAX_INPUT_TOKENS = 100_000
_OVERHEAD_PER_COMMENT = 10  # "COMMENT N: " + newline when formatting for prompt


class FeedbackChunkingService:
    """Service for chunking feedback data optimally for AI analysis."""
    
    def __init__(self):
        self.default_model = get_azure_deployment_name()
        raw = os.getenv("MAX_INPUT_TOKENS_PER_BATCH", str(_DEFAULT_MAX_INPUT_TOKENS))
        try:
            self.max_input_tokens_per_batch = max(4_000, min(128_000, int(raw)))
        except (TypeError, ValueError):
            self.max_input_tokens_per_batch = _DEFAULT_MAX_INPUT_TOKENS
        self.overlap = 100  # Used only by _chunk_text_standard fallback
    
    def chunk_feedback_for_sentiment(self, feedback_data: str, model: str = None) -> List[str]:
        """
        Chunk feedback data optimally for sentiment analysis.
        
        Uses token-based batching: max input tokens per batch; comments are never split.
        If adding a comment would exceed the limit, it goes to the next batch.
        
        Args:
            feedback_data: Raw feedback text
            model: AI model name (defaults to gpt-5-mini)
            
        Returns:
            List of text chunks optimized for sentiment analysis
        """
        model = model or self.default_model
        
        try:
            comments = self._split_into_comments(feedback_data)
            if len(comments) > 1:
                return self._chunk_by_token_limit(comments, model)
            logger.warning("Could not identify comment boundaries; using standard token chunking")
            return self._chunk_text_standard(
                feedback_data, model, self.max_input_tokens_per_batch, self.overlap
            )
        except Exception:
            logger.warning("Smart chunking failed; falling back to standard chunking")
            return self._chunk_text_standard(
                feedback_data, model, self.max_input_tokens_per_batch, self.overlap
            )
    
    def chunk_feedback_for_deep_analysis(self, feedback_data: str, model: str = None) -> List[str]:
        """
        Chunk feedback data optimally for deep analysis and work item generation.
        
        Uses token-based batching: max input tokens per batch; comments are never split.
        
        Args:
            feedback_data: Raw feedback text
            model: AI model name (defaults to gpt-5-mini)
            
        Returns:
            List of text chunks optimized for deep analysis
        """
        model = model or self.default_model
        
        try:
            comments = self._split_into_comments(feedback_data)
            if len(comments) > 1:
                return self._chunk_by_token_limit(comments, model)
            logger.warning("Could not identify comment boundaries; using standard token chunking")
            return self._chunk_text_standard(
                feedback_data, model, self.max_input_tokens_per_batch, self.overlap * 2
            )
        except Exception:
            logger.warning("Smart chunking failed; falling back to standard chunking")
            return self._chunk_text_standard(
                feedback_data, model, self.max_input_tokens_per_batch, self.overlap * 2
            )
    
    def _split_into_comments(self, feedback_data: str) -> List[str]:
        """
        Try to intelligently split feedback into individual comments.
        
        Looks for common patterns like:
        - JSON array of comments
        - CSV-like structure
        - Line-separated comments
        - Numbered comments
        """
        # Try JSON array first
        if feedback_data.strip().startswith('[') and feedback_data.strip().endswith(']'):
            try:
                import json
                data = json.loads(feedback_data)
                if isinstance(data, list):
                    return [str(item) for item in data if item]
            except (ValueError, TypeError):
                pass  # not JSON — fall through to delimiter-based splitting
        
        # Try line separation with common delimiters
        lines = feedback_data.split('\n')
        if len(lines) > 1:
            # Filter out only empty lines (not short lines - short comments are valid!)
            comments = [line.strip() for line in lines if line.strip()]
            if len(comments) > 1:
                return comments
        
        # Try numbered comments (1., 2., etc.)
        numbered_pattern = r'^\d+\.\s*(.+)'
        numbered_comments = []
        for line in lines:
            match = re.match(numbered_pattern, line.strip())
            if match:
                numbered_comments.append(match.group(1))
        
        if len(numbered_comments) > 1:
            return numbered_comments
        
        # If no clear structure, return as single item
        return [feedback_data]
    
    def _chunk_by_token_limit(self, comments: List[str], model: str) -> List[str]:
        """
        Batch comments by max input tokens per API call. Never split a comment;
        if adding it would exceed the limit, it goes to the next batch.
        """
        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")
        
        chunks: List[str] = []
        current_batch: List[str] = []
        current_tokens = 0
        limit = self.max_input_tokens_per_batch
        
        for i, comment in enumerate(comments):
            raw_tokens = len(encoding.encode(comment))
            effective = raw_tokens + _OVERHEAD_PER_COMMENT
            
            if raw_tokens > limit:
                logger.warning(
                    "Comment %d exceeds token limit (%d > %d); putting in its own batch",
                    i, raw_tokens, limit,
                )
            if current_tokens + effective > limit and current_batch:
                chunks.append("\n".join(current_batch))
                current_batch = [comment]
                current_tokens = raw_tokens + _OVERHEAD_PER_COMMENT
            else:
                current_batch.append(comment)
                current_tokens += effective
        
        if current_batch:
            chunks.append("\n".join(current_batch))
        
        logger.debug(
            "Chunked %d comments into %d batches (max %d input tokens per batch)",
            len(comments), len(chunks), limit,
        )
        return chunks
    
    def _chunk_text_standard(self, text: str, model: str, max_tokens: int, overlap: int) -> List[str]:
        """
        Standard text chunking with overlap (original chunker logic).
        
        This is the fallback method when smart chunking isn't possible
        (e.g., unstructured text without clear comment boundaries).
        """
        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            # Fallback to a common encoding if model not found
            encoding = tiktoken.get_encoding("cl100k_base")
        
        encoded_text = encoding.encode(text)
        
        chunks = []
        start = 0
        while start < len(encoded_text):
            end = start + max_tokens
            chunk = encoded_text[start:end]
            chunks.append(encoding.decode(chunk))
            start = end - overlap  # Move the start forward with overlap
        
        return chunks
    
    def get_chunk_info(self, feedback_data: str, model: str = None) -> Dict[str, Any]:
        """
        Get information about how feedback would be chunked.
        
        Useful for debugging and optimization.
        """
        model = model or self.default_model
        
        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")
        
        comments = self._split_into_comments(feedback_data)
        total_tokens = len(encoding.encode(feedback_data))
        
        sentiment_chunks = self.chunk_feedback_for_sentiment(feedback_data, model)
        deep_analysis_chunks = self.chunk_feedback_for_deep_analysis(feedback_data, model)
        
        sentiment_chunk_sizes = [len(c.split("\n")) for c in sentiment_chunks]
        deep_chunk_sizes = [len(c.split("\n")) for c in deep_analysis_chunks]
        
        return {
            "total_comments": len(comments),
            "total_tokens": total_tokens,
            "total_characters": len(feedback_data),
            "max_input_tokens_per_batch": self.max_input_tokens_per_batch,
            "sentiment_analysis": {
                "chunk_count": len(sentiment_chunks),
                "comments_per_chunk": sentiment_chunk_sizes,
                "avg_comments_per_chunk": sum(sentiment_chunk_sizes) / len(sentiment_chunk_sizes) if sentiment_chunk_sizes else 0,
            },
            "deep_analysis": {
                "chunk_count": len(deep_analysis_chunks),
                "comments_per_chunk": deep_chunk_sizes,
                "avg_comments_per_chunk": sum(deep_chunk_sizes) / len(deep_chunk_sizes) if deep_chunk_sizes else 0,
            },
        }


# Global service instance
_chunking_service = None

def get_chunking_service() -> FeedbackChunkingService:
    """Get the global chunking service instance."""
    global _chunking_service
    if _chunking_service is None:
        _chunking_service = FeedbackChunkingService()
    return _chunking_service
