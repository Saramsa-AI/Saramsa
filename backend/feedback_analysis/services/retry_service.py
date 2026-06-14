"""
Retry Service for Batch Processing

Handles automatic retry logic for batch processing failures.
Ensures transient errors are automatically retried without manual intervention.
"""

import time
import logging
from typing import Callable, Any, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class ErrorType(Enum):
    """Classification of errors for retry decision."""
    TRANSIENT = "transient"  # Should retry: rate limits, timeouts, network errors
    PERMANENT = "permanent"  # Should NOT retry: auth errors, invalid data
    UNKNOWN = "unknown"  # Default to retry for safety


class BatchRetryService:
    """Service for handling retries of failed batch processing operations."""
    
    # Configuration
    MAX_RETRIES = 3  # Maximum retries per batch
    INITIAL_RETRY_DELAY = 1  # Initial delay in seconds
    MAX_RETRY_DELAY = 60  # Maximum delay in seconds
    EXPONENTIAL_BASE = 2  # Exponential backoff multiplier
    
    @staticmethod
    def classify_error(error: Exception) -> ErrorType:
        """
        Classify error type to determine if retry is appropriate.
        
        Args:
            error: Exception that occurred
            
        Returns:
            ErrorType: Classification of the error
        """
        error_str = str(error).lower()
        error_type = type(error).__name__
        
        # Transient errors (should retry)
        transient_indicators = [
            'rate limit',
            '429',
            'timeout',
            'timed out',
            'connection',
            'network',
            'temporary',
            '503',
            '502',
            '504',
            'server error',
            'service unavailable',
            'too many requests'
        ]
        
        # Permanent errors (should NOT retry)
        permanent_indicators = [
            '401',
            'unauthorized',
            '403',
            'forbidden',
            '400',
            'bad request',
            'invalid',
            'authentication',
            'authorization',
            'malformed',
            'parse error'
        ]
        
        # Check for transient errors
        if any(indicator in error_str for indicator in transient_indicators):
            return ErrorType.TRANSIENT
        
        # Check for permanent errors
        if any(indicator in error_str for indicator in permanent_indicators):
            return ErrorType.PERMANENT
        
        # Check exception types
        transient_exceptions = (
            'TimeoutError',
            'ConnectionError',
            'ConnectTimeout',
            'ReadTimeout',
            'HTTPError',  # Will check status code separately
        )
        
        if error_type in transient_exceptions:
            # Check if it's an HTTP error with specific status code
            if hasattr(error, 'response'):
                status_code = getattr(error.response, 'status_code', None)
                if status_code in [429, 502, 503, 504]:
                    return ErrorType.TRANSIENT
                elif status_code in [400, 401, 403]:
                    return ErrorType.PERMANENT
        
        # Default to unknown (will retry for safety)
        return ErrorType.UNKNOWN
    
    @staticmethod
    def calculate_retry_delay(attempt: int, initial_delay: float = None, 
                             max_delay: float = None, base: float = None) -> float:
        """
        Calculate exponential backoff delay for retry.
        
        Args:
            attempt: Retry attempt number (0-indexed)
            initial_delay: Initial delay in seconds (default: INITIAL_RETRY_DELAY)
            max_delay: Maximum delay in seconds (default: MAX_RETRY_DELAY)
            base: Exponential base (default: EXPONENTIAL_BASE)
            
        Returns:
            Delay in seconds
        """
        initial_delay = initial_delay or BatchRetryService.INITIAL_RETRY_DELAY
        max_delay = max_delay or BatchRetryService.MAX_RETRY_DELAY
        base = base or BatchRetryService.EXPONENTIAL_BASE
        
        delay = initial_delay * (base ** attempt)
        return min(delay, max_delay)
    
    @classmethod
    def retry_batch_operation(cls, operation: Callable, batch_index: int,
                              max_retries: int = None, operation_name: str = "batch operation") -> Tuple[Any, bool]:
        """
        Execute operation with automatic retry logic.
        
        Args:
            operation: Callable that performs the operation (should return result)
            batch_index: Index of the batch (for logging)
            max_retries: Maximum retry attempts (default: MAX_RETRIES)
            operation_name: Name of operation (for logging)
            
        Returns:
            Tuple of (result, success): result is the operation result or error dict, success is bool
        """
        max_retries = max_retries or cls.MAX_RETRIES
        last_error = None
        
        for attempt in range(max_retries + 1):  # +1 for initial attempt
            try:
                result = operation()
                if attempt > 0:
                    logger.info(
                        "Batch operation succeeded after retries",
                        extra={"operation": operation_name, "batch_index": batch_index, "retries": attempt},
                    )
                return result, True
                
            except Exception as e:
                last_error = e
                error_type = cls.classify_error(e)
                
                # Don't retry permanent errors
                if error_type == ErrorType.PERMANENT:
                    logger.exception(
                        "Failed to run batch operation: permanent error, no retries attempted",
                        extra={"operation": operation_name, "batch_index": batch_index},
                    )
                    return {
                        "chunk_index": batch_index,
                        "error": str(e),
                        "error_type": "permanent",
                        "processed": False
                    }, False
                
                # Check if we've exhausted retries
                if attempt >= max_retries:
                    logger.exception(
                        "Failed to run batch operation: retries exhausted",
                        extra={"operation": operation_name, "batch_index": batch_index, "max_retries": max_retries},
                    )
                    return {
                        "chunk_index": batch_index,
                        "error": str(e),
                        "error_type": error_type.value,
                        "attempts": attempt + 1,
                        "processed": False
                    }, False
                
                # Calculate delay for next retry
                delay = cls.calculate_retry_delay(attempt)
                logger.warning(
                    "Retrying batch operation",
                    extra={
                        "operation": operation_name,
                        "batch_index": batch_index,
                        "attempt": attempt + 1,
                        "max_attempts": max_retries + 1,
                        "delay_s": round(delay, 1),
                        "error_type": error_type.value,
                    },
                )
                
                time.sleep(delay)
        
        # Should never reach here, but just in case
        return {
            "chunk_index": batch_index,
            "error": str(last_error) if last_error else "Unknown error",
            "error_type": "unknown",
            "processed": False
        }, False


# Global service instance
_retry_service = None

def get_retry_service() -> BatchRetryService:
    """Get the global retry service instance."""
    global _retry_service
    if _retry_service is None:
        _retry_service = BatchRetryService()
    return _retry_service
