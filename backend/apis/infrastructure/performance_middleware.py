"""
Performance monitoring middleware for detailed request tracking.
"""

import time
import logging
import os
from django.utils.deprecation import MiddlewareMixin
from django.conf import settings
from .cache_service import get_cache_service
from .storage_service import storage_service
import json
from typing import Dict, Any

logger = logging.getLogger(__name__)

class PerformanceTrackingMiddleware(MiddlewareMixin):
    """
    Advanced performance tracking middleware with detailed metrics.
    """
    
    def __init__(self, get_response):
        super().__init__(get_response)
        self.get_response = get_response
        self.cache_service = get_cache_service()
        self.slow_request_threshold = float(os.getenv('SLOW_REQUEST_THRESHOLD', '2.0'))  # seconds
        self.very_slow_request_threshold = float(os.getenv('VERY_SLOW_REQUEST_THRESHOLD', '5.0'))  # seconds

    def process_request(self, request):
        """Start performance tracking."""
        request.perf_start_time = time.time()
        request.perf_data = {
            'method': request.method,
            'path': request.path,
            'user_agent': request.META.get('HTTP_USER_AGENT', '')[:100],
            'remote_addr': self._get_client_ip(request),
            'content_length': request.META.get('CONTENT_LENGTH', 0)
        }
        return None

    def process_response(self, request, response):
        """Complete performance tracking and log metrics."""
        if not hasattr(request, 'perf_start_time'):
            return response
        
        # Calculate timing
        end_time = time.time()
        duration = end_time - request.perf_start_time
        
        # Gather performance data
        perf_data = request.perf_data
        perf_data.update({
            'duration_ms': round(duration * 1000, 2),
            'status_code': response.status_code,
            'response_size': len(response.content) if hasattr(response, 'content') else 0,
            'timestamp': time.time()
        })
        
        # Add database and cache stats if available
        try:
            db_stats = storage_service.get_performance_stats()
            cache_stats = self.cache_service.get_stats()
            perf_data.update({
                'db_requests': db_stats.get('total_requests', 0),
                'db_success_rate': db_stats.get('success_rate_percent', 0),
                'cache_hit_rate': cache_stats.get('hit_rate_percent', 0)
            })
        except Exception:
            logger.debug("Failed to gather service stats", exc_info=True)
        
        # Log based on performance thresholds
        if duration >= self.very_slow_request_threshold:
            logger.warning("Very slow request", extra={"perf": perf_data})
        elif duration >= self.slow_request_threshold:
            logger.info("Slow request", extra={"perf": perf_data})
        elif response.status_code >= 400:
            logger.info("Error response", extra={"perf": perf_data})
        else:
            logger.debug(
                "Request completed",
                extra={
                    "method": perf_data['method'],
                    "path": perf_data['path'],
                    "duration_ms": perf_data['duration_ms'],
                },
            )
        
        # Store performance data in cache for monitoring dashboard
        self._store_performance_data(perf_data)
        
        # Add performance headers for debugging
        if settings.DEBUG:
            response['X-Response-Time'] = f"{perf_data['duration_ms']}ms"
            response['X-Request-ID'] = getattr(request, 'request_id', 'unknown')
        
        return response

    def _get_client_ip(self, request):
        """Extract client IP address from request."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR', 'unknown')
        return ip

    def _store_performance_data(self, perf_data: Dict[str, Any]):
        """Store performance data for monitoring."""
        try:
            # Store recent performance data (last 100 requests)
            cache_key = "performance:recent_requests"
            recent_requests = self.cache_service.get(cache_key, [])
            
            # Keep only last 99 requests, add new one
            if len(recent_requests) >= 100:
                recent_requests = recent_requests[-99:]
            recent_requests.append(perf_data)
            
            # Store for 1 hour
            self.cache_service.set(cache_key, recent_requests, ttl=3600)
            
            # Update aggregate stats
            self._update_aggregate_stats(perf_data)
            
        except Exception:
            logger.exception("Failed to store performance data")

    def _update_aggregate_stats(self, perf_data: Dict[str, Any]):
        """Update aggregate performance statistics."""
        try:
            stats_key = "performance:aggregate_stats"
            stats = self.cache_service.get(stats_key, {
                'total_requests': 0,
                'total_duration_ms': 0,
                'slow_requests': 0,
                'very_slow_requests': 0,
                'error_requests': 0,
                'status_codes': {},
                'endpoints': {},
                'last_updated': time.time()
            })
            
            # Update counters
            stats['total_requests'] += 1
            stats['total_duration_ms'] += perf_data['duration_ms']
            
            if perf_data['duration_ms'] >= self.very_slow_request_threshold * 1000:
                stats['very_slow_requests'] += 1
            elif perf_data['duration_ms'] >= self.slow_request_threshold * 1000:
                stats['slow_requests'] += 1
            
            if perf_data['status_code'] >= 400:
                stats['error_requests'] += 1
            
            # Track status codes
            status_code = str(perf_data['status_code'])
            stats['status_codes'][status_code] = stats['status_codes'].get(status_code, 0) + 1
            
            # Track endpoints
            endpoint = f"{perf_data['method']} {perf_data['path']}"
            if endpoint not in stats['endpoints']:
                stats['endpoints'][endpoint] = {
                    'count': 0,
                    'total_duration_ms': 0,
                    'avg_duration_ms': 0
                }
            
            endpoint_stats = stats['endpoints'][endpoint]
            endpoint_stats['count'] += 1
            endpoint_stats['total_duration_ms'] += perf_data['duration_ms']
            endpoint_stats['avg_duration_ms'] = endpoint_stats['total_duration_ms'] / endpoint_stats['count']
            
            stats['last_updated'] = time.time()
            
            # Store for 24 hours
            self.cache_service.set(stats_key, stats, ttl=86400)
            
        except Exception:
            logger.exception("Failed to update aggregate stats")


class DatabaseQueryCountMiddleware(MiddlewareMixin):
    """
    Middleware to track database query counts (for PostgreSQL operations).
    """
    
    def process_request(self, request):
        """Initialize query tracking."""
        request.db_query_count = 0
        return None

    def process_response(self, request, response):
        """Log query count if significant."""
        if hasattr(request, 'db_query_count') and request.db_query_count > 10:
            logger.warning(
                "High PostgreSQL query count",
                extra={
                    "query_count": request.db_query_count,
                    "method": request.method,
                    "path": request.path,
                },
            )
        return response


def get_performance_summary() -> Dict[str, Any]:
    """
    Get performance summary from cache.
    
    Returns:
        Dictionary with performance metrics
    """
    cache_service = get_cache_service()
    
    # Get aggregate stats
    aggregate_stats = cache_service.get("performance:aggregate_stats", {})
    
    # Get recent requests
    recent_requests = cache_service.get("performance:recent_requests", [])
    
    # Calculate additional metrics
    if aggregate_stats and aggregate_stats.get('total_requests', 0) > 0:
        avg_response_time = aggregate_stats['total_duration_ms'] / aggregate_stats['total_requests']
        error_rate = (aggregate_stats.get('error_requests', 0) / aggregate_stats['total_requests']) * 100
        slow_request_rate = (aggregate_stats.get('slow_requests', 0) / aggregate_stats['total_requests']) * 100
    else:
        avg_response_time = 0
        error_rate = 0
        slow_request_rate = 0
    
    return {
        'summary': {
            'total_requests': aggregate_stats.get('total_requests', 0),
            'avg_response_time_ms': round(avg_response_time, 2),
            'error_rate_percent': round(error_rate, 2),
            'slow_request_rate_percent': round(slow_request_rate, 2),
            'last_updated': aggregate_stats.get('last_updated')
        },
        'status_codes': aggregate_stats.get('status_codes', {}),
        'top_endpoints': sorted(
            aggregate_stats.get('endpoints', {}).items(),
            key=lambda x: x[1]['count'],
            reverse=True
        )[:10],
        'recent_requests_count': len(recent_requests),
        'cache_stats': cache_service.get_stats(),
        'db_stats': storage_service.get_performance_stats() if storage_service.is_enabled else {}
    }
