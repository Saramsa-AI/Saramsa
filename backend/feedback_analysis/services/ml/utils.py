"""
Utility functions for the local ML pipeline.

This module provides core utility functions for text processing, similarity computation,
and comment sampling used throughout the ML pipeline.
"""

import re
import numpy as np
from typing import List, Dict, Any, Tuple
from sklearn.metrics.pairwise import cosine_similarity as sklearn_cosine_similarity

from .config import REPRESENTATIVE_COMMENTS_SAMPLE_SIZE, get_logger

logger = get_logger("utils")


def cosine_similarity(x1: np.ndarray, x2: np.ndarray) -> np.ndarray:
    """
    Compute cosine similarity between two arrays.
    
    Args:
        x1: First array of shape (n_samples_1, n_features)
        x2: Second array of shape (n_samples_2, n_features)
        
    Returns:
        Similarity matrix of shape (n_samples_1, n_samples_2)
    """
    return sklearn_cosine_similarity(x1, x2)


def batch_cosine_similarity(embeddings1: List[np.ndarray], embeddings2: List[np.ndarray]) -> np.ndarray:
    """
    Compute cosine similarity between two batches of embeddings.
    
    Args:
        embeddings1: List of embedding arrays
        embeddings2: List of embedding arrays
        
    Returns:
        Similarity matrix of shape (len(embeddings1), len(embeddings2))
    """
    if not embeddings1 or not embeddings2:
        return np.array([])
    
    # Stack embeddings into matrices
    matrix1 = np.vstack(embeddings1)
    matrix2 = np.vstack(embeddings2)
    
    return cosine_similarity(matrix1, matrix2)


def preprocess_text_for_sentiment(text: str) -> str:
    """
    Preprocess text for sentiment analysis.
    
    Performs basic cleaning while preserving sentiment-relevant information:
    - Normalizes whitespace
    - Removes excessive punctuation
    - Preserves emoticons and sentiment indicators
    
    Args:
        text: Raw text to preprocess
        
    Returns:
        Cleaned text suitable for sentiment analysis
    """
    if not text or not isinstance(text, str):
        return ""
    
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text.strip())
    
    # Remove excessive punctuation (more than 3 consecutive)
    text = re.sub(r'([!?.]){4,}', r'\1\1\1', text)
    
    # Normalize common contractions for better sentiment detection
    contractions = {
        "won't": "will not",
        "can't": "cannot",
        "n't": " not",
        "'re": " are",
        "'ve": " have",
        "'ll": " will",
        "'d": " would",
        "'m": " am"
    }
    
    for contraction, expansion in contractions.items():
        text = text.replace(contraction, expansion)
    
    # Remove extra spaces after preprocessing
    text = re.sub(r'\s+', ' ', text.strip())
    
    return text


def sample_representative_comments(
    comments: List[Dict[str, Any]], 
    max_samples: int = None,
    prioritize_confidence: bool = True,
    ensure_sentiment_diversity: bool = True
) -> List[Dict[str, Any]]:
    """
    Sample representative comments for synthesis or analysis.
    
    Selects a diverse set of comments that best represent the overall feedback,
    prioritizing high-confidence predictions and sentiment diversity.
    
    Args:
        comments: List of comment dictionaries with sentiment and confidence info
        max_samples: Maximum number of samples to return (default from config)
        prioritize_confidence: Whether to prioritize high-confidence comments
        ensure_sentiment_diversity: Whether to ensure diverse sentiment representation
        
    Returns:
        List of sampled comment dictionaries
    """
    if not comments:
        return []
    
    if max_samples is None:
        max_samples = REPRESENTATIVE_COMMENTS_SAMPLE_SIZE
    
    if len(comments) <= max_samples:
        return comments.copy()
    
    # Create working copy with indices
    indexed_comments = [(i, comment) for i, comment in enumerate(comments)]
    
    if prioritize_confidence:
        # Sort by confidence (HIGH > MEDIUM > LOW)
        confidence_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        indexed_comments.sort(
            key=lambda x: confidence_order.get(
                x[1].get("confidence", "LOW"), 3
            )
        )
    
    selected = []
    sentiment_counts = {"POSITIVE": 0, "NEGATIVE": 0, "NEUTRAL": 0}
    
    if ensure_sentiment_diversity:
        # First pass: ensure at least one of each sentiment if available
        sentiments_needed = set(sentiment_counts.keys())
        
        for idx, comment in indexed_comments:
            if len(selected) >= max_samples:
                break
                
            sentiment = comment.get("sentiment", "NEUTRAL")
            if sentiment in sentiments_needed:
                selected.append(comment)
                sentiment_counts[sentiment] += 1
                sentiments_needed.discard(sentiment)
        
        # Second pass: fill remaining slots
        remaining_slots = max_samples - len(selected)
        if remaining_slots > 0:
            for idx, comment in indexed_comments:
                if len(selected) >= max_samples:
                    break
                    
                # Skip if already selected
                if any(c.get("comment_id") == comment.get("comment_id") for c in selected):
                    continue
                
                selected.append(comment)
    else:
        # Simple selection without diversity constraints
        selected = [comment for _, comment in indexed_comments[:max_samples]]
    
    logger.debug(
        "Sampled representative comments",
        extra={
            "sample_count": len(selected),
            "total_comments": len(comments),
            "sentiment_distribution": dict(sentiment_counts),
        },
    )

    return selected


def extract_keywords_from_text(text: str, min_length: int = 3, max_keywords: int = 10) -> List[str]:
    """
    Extract keywords from text using simple frequency analysis.
    
    Args:
        text: Input text
        min_length: Minimum word length to consider
        max_keywords: Maximum number of keywords to return
        
    Returns:
        List of extracted keywords
    """
    if not text:
        return []
    
    # Simple stopwords (common English words to exclude)
    stopwords = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with',
        'by', 'is', 'are', 'was', 'were', 'be', 'been', 'have', 'has', 'had', 'do', 'does',
        'did', 'will', 'would', 'could', 'should', 'may', 'might', 'can', 'this', 'that',
        'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they', 'me', 'him', 'her',
        'us', 'them', 'my', 'your', 'his', 'her', 'its', 'our', 'their', 'not', 'no', 'yes'
    }
    
    # Extract words (alphanumeric, minimum length)
    words = re.findall(r'\b[a-zA-Z]{' + str(min_length) + r',}\b', text.lower())
    
    # Filter stopwords and count frequency
    word_freq = {}
    for word in words:
        if word not in stopwords:
            word_freq[word] = word_freq.get(word, 0) + 1
    
    # Sort by frequency and return top keywords
    sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
    return [word for word, freq in sorted_words[:max_keywords]]


def normalize_confidence_score(confidence: str) -> float:
    """
    Convert confidence string to numeric score.
    
    Args:
        confidence: Confidence level string (HIGH, MEDIUM, LOW)
        
    Returns:
        Numeric confidence score between 0 and 1
    """
    confidence_map = {
        "HIGH": 0.9,
        "MEDIUM": 0.6,
        "LOW": 0.3,
    }
    
    return confidence_map.get(str(confidence).upper(), 0.0)


def calculate_sentiment_distribution(sentiments: List[str]) -> Dict[str, float]:
    """
    Calculate sentiment distribution as percentages.
    
    Args:
        sentiments: List of sentiment strings
        
    Returns:
        Dictionary with sentiment percentages
    """
    if not sentiments:
        return {"POSITIVE": 0.0, "NEGATIVE": 0.0, "NEUTRAL": 0.0}

    total = len(sentiments)
    counts = {"POSITIVE": 0, "NEGATIVE": 0, "NEUTRAL": 0}
    
    for sentiment in sentiments:
        sentiment_key = str(sentiment).upper()
        if sentiment_key in counts:
            counts[sentiment_key] += 1
    
    return {k: (v / total) * 100 for k, v in counts.items()}
