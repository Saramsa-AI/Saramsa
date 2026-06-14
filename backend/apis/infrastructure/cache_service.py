"""
Caching service for performance optimization.
Provides Redis-based caching.
Supports both local Redis and Azure Redis Cache with SSL.
"""

import json
import logging
import hashlib
import ssl
from typing import Any, Optional, Dict, List
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlparse, parse_qs, unquote
import os

logger = logging.getLogger(__name__)

# Try to import Redis
try:
    import redis
    from redis.retry import Retry
    from redis.backoff import ExponentialBackoff
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.error("Redis dependency is not installed")


def _resilience_kwargs() -> dict:
    """Client timeouts + bounded retry so a Redis blip fails in seconds, not the
    OS-default ~135s. Tuned for small scale (~50-100 customers). Follows Azure
    Cache for Redis connection-resilience guidance: 5s connect, <5s command,
    keepalive under the 10-min idle reaper, and a single retry (fail fast, not
    the durability-oriented 25-retry default)."""
    return dict(
        socket_connect_timeout=5,
        socket_timeout=3,
        socket_keepalive=True,
        health_check_interval=30,
        max_connections=30,
        retry=Retry(ExponentialBackoff(cap=2, base=0.25), 1),
        retry_on_error=[redis.exceptions.ConnectionError, redis.exceptions.TimeoutError],
    )

class CacheService:
    """
    Unified caching service with Redis backend.
    Supports both local Redis and Azure Redis Cache with SSL.
    """
    
    def __init__(self):
        self.redis_client = None
        self.memory_cache = {}
        self.allow_in_memory_fallback = os.getenv('ALLOW_IN_MEMORY_CACHE', 'false').lower() == 'true'
        self.cache_stats = {
            'hits': 0,
            'misses': 0,
            'sets': 0,
            'deletes': 0
        }

        if not REDIS_AVAILABLE:
            if self.allow_in_memory_fallback:
                logger.warning("Redis not available; using in-memory cache because ALLOW_IN_MEMORY_CACHE is true")
                return
            raise RuntimeError("Redis dependency is not installed and ALLOW_IN_MEMORY_CACHE is false.")

        try:
            redis_url = os.getenv('REDIS_URL')

            if not redis_url:
                if self.allow_in_memory_fallback:
                    logger.warning("REDIS_URL not set; using in-memory cache because ALLOW_IN_MEMORY_CACHE is true")
                    return
                raise RuntimeError("REDIS_URL environment variable is required when ALLOW_IN_MEMORY_CACHE=false.")

            # Support both local Redis (redis://) and Azure Redis (rediss://)
            is_ssl = redis_url.startswith('rediss://')

            if is_ssl:
                # Parse URL for Azure Redis with SSL
                parsed = urlparse(redis_url)
                host = parsed.hostname
                port = parsed.port or 6380  # Default SSL port for Azure Redis
                # Handle password (may be URL encoded)
                password = parsed.password
                if password:
                    password = unquote(password)

                # Use Redis() constructor directly as from_url() doesn't handle ssl_cert_reqs properly
                self.redis_client = redis.Redis(
                    host=host,
                    port=port,
                    password=password,
                    decode_responses=True,
                    ssl=True,
                    ssl_cert_reqs=ssl.CERT_NONE,  # Controlled globally in Django settings transport opts
                    ssl_ca_certs=None,
                    ssl_certfile=None,
                    ssl_keyfile=None,
                    **_resilience_kwargs(),
                )
                logger.info("Connecting to Azure Redis (SSL)")
            else:
                # Local Redis without SSL
                if not redis_url.startswith('redis://'):
                    raise RuntimeError(f"REDIS_URL should start with redis:// or rediss://. Got: {redis_url[:20]}...")

                self.redis_client = redis.from_url(
                    redis_url,
                    decode_responses=True,
                    **_resilience_kwargs(),
                )
                logger.info("Connecting to local Redis (non-SSL)")

            # Test connection
            self.redis_client.ping()
            logger.info("Redis cache initialized", extra={"ssl": is_ssl})
        except Exception as e:
            if self.allow_in_memory_fallback:
                logger.warning(
                    "Redis connection failed; using in-memory cache because ALLOW_IN_MEMORY_CACHE is true",
                    exc_info=True,
                )
                self.redis_client = None
                return
            raise RuntimeError(f"Redis connection failed and ALLOW_IN_MEMORY_CACHE=false: {e}") from e
    
    def _generate_key(self, key: str, prefix: str = "saramsa") -> str:
        """Generate a prefixed cache key."""
        return f"{prefix}:{key}"
    
    def _serialize_value(self, value: Any) -> str:
        """Serialize value for storage."""
        return json.dumps(value, default=str)
    
    def _deserialize_value(self, value: str) -> Any:
        """Deserialize value from storage."""
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get value from cache.
        
        Args:
            key: Cache key
            default: Default value if key not found
            
        Returns:
            Cached value or default
        """
        cache_key = self._generate_key(key)
        
        try:
            if self.redis_client:
                value = self.redis_client.get(cache_key)
                if value is not None:
                    self.cache_stats['hits'] += 1
                    return self._deserialize_value(value)
            else:
                # In-memory cache with expiration check
                if cache_key in self.memory_cache:
                    cached_item = self.memory_cache[cache_key]
                    if cached_item['expires_at'] > datetime.now():
                        self.cache_stats['hits'] += 1
                        return cached_item['value']
                    else:
                        # Expired, remove from cache
                        del self.memory_cache[cache_key]
            
            self.cache_stats['misses'] += 1
            return default
            
        except Exception:
            logger.exception("Failed to get cache key", extra={"cache_key": key})
            self.cache_stats['misses'] += 1
            return default
    
    def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """
        Set value in cache.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live in seconds
            
        Returns:
            True if successful, False otherwise
        """
        cache_key = self._generate_key(key)
        
        try:
            if self.redis_client:
                serialized_value = self._serialize_value(value)
                result = self.redis_client.setex(cache_key, ttl, serialized_value)
                if result:
                    self.cache_stats['sets'] += 1
                return result
            else:
                # In-memory cache
                expires_at = datetime.now() + timedelta(seconds=ttl)
                self.memory_cache[cache_key] = {
                    'value': value,
                    'expires_at': expires_at
                }
                self.cache_stats['sets'] += 1
                return True
                
        except Exception:
            logger.exception("Failed to set cache key", extra={"cache_key": key})
            return False
    
    def delete(self, key: str) -> bool:
        """
        Delete value from cache.
        
        Args:
            key: Cache key
            
        Returns:
            True if successful, False otherwise
        """
        cache_key = self._generate_key(key)
        
        try:
            if self.redis_client:
                result = self.redis_client.delete(cache_key)
                if result:
                    self.cache_stats['deletes'] += 1
                return bool(result)
            else:
                if cache_key in self.memory_cache:
                    del self.memory_cache[cache_key]
                    self.cache_stats['deletes'] += 1
                    return True
                return False
                
        except Exception:
            logger.exception("Failed to delete cache key", extra={"cache_key": key})
            return False

    def incr(self, key: str, amount: int = 1, ttl: int = 3600) -> int:
        """Increment a counter in cache."""
        cache_key = self._generate_key(key)
        try:
            if self.redis_client:
                value = self.redis_client.incrby(cache_key, amount)
                if ttl:
                    self.redis_client.expire(cache_key, ttl)
                return int(value)
            # In-memory counter
            current = 0
            if cache_key in self.memory_cache:
                current = int(self.memory_cache[cache_key].get('value', 0))
            current += amount
            self.memory_cache[cache_key] = {
                'value': current,
                'expires_at': datetime.now() + timedelta(seconds=ttl)
            }
            return current
        except Exception:
            logger.exception("Failed to increment cache key", extra={"cache_key": key})
            return 0
    
    def clear_pattern(self, pattern: str) -> int:
        """
        Clear all keys matching a pattern.
        
        Args:
            pattern: Pattern to match (e.g., "user:*")
            
        Returns:
            Number of keys deleted
        """
        full_pattern = self._generate_key(pattern)
        deleted_count = 0
        
        try:
            if self.redis_client:
                keys = self.redis_client.keys(full_pattern)
                if keys:
                    deleted_count = self.redis_client.delete(*keys)
            else:
                # In-memory cache pattern matching
                keys_to_delete = [k for k in self.memory_cache.keys() if self._matches_pattern(k, full_pattern)]
                for key in keys_to_delete:
                    del self.memory_cache[key]
                deleted_count = len(keys_to_delete)
            
            self.cache_stats['deletes'] += deleted_count
            return deleted_count
            
        except Exception:
            logger.exception("Failed to clear cache pattern", extra={"cache_pattern": pattern})
            return 0
    
    def _matches_pattern(self, key: str, pattern: str) -> bool:
        """Simple pattern matching for in-memory cache."""
        import fnmatch
        return fnmatch.fnmatch(key, pattern)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total_requests = self.cache_stats['hits'] + self.cache_stats['misses']
        hit_rate = (self.cache_stats['hits'] / total_requests * 100) if total_requests > 0 else 0
        
        stats = {
            **self.cache_stats,
            'hit_rate_percent': round(hit_rate, 2),
            'backend': 'redis' if self.redis_client else 'memory',
            'memory_cache_size': len(self.memory_cache) if not self.redis_client else None
        }
        
        if self.redis_client:
            try:
                info = self.redis_client.info()
                stats['redis_info'] = {
                    'used_memory_human': info.get('used_memory_human'),
                    'connected_clients': info.get('connected_clients'),
                    'total_commands_processed': info.get('total_commands_processed')
                }
            except Exception:
                logger.exception("Failed to get Redis info")

        return stats
    
    def health_check(self) -> Dict[str, Any]:
        """Check cache health with detailed Redis diagnostics."""
        import time as _time

        if not self.redis_client:
            return {
                'status': 'healthy',
                'backend': 'memory',
                'cache_stats': self.cache_stats,
            }

        try:
            # Latency
            start = _time.time()
            self.redis_client.ping()
            latency_ms = round((_time.time() - start) * 1000, 1)

            # Server info
            server_info = self.redis_client.info('server')
            memory_info = self.redis_client.info('memory')
            clients_info = self.redis_client.info('clients')

            # Read/write probe
            probe_key = 'saramsa:health_probe'
            self.redis_client.set(probe_key, 'ok', ex=30)
            probe_ok = self.redis_client.get(probe_key) == 'ok'

            result: Dict[str, Any] = {
                'status': 'healthy',
                'backend': 'redis',
                'host': self.redis_client.connection_pool.connection_kwargs.get('host', 'unknown'),
                'port': self.redis_client.connection_pool.connection_kwargs.get('port', 'unknown'),
                'ssl': self.redis_client.connection_pool.connection_kwargs.get('ssl', False),
                'latency_ms': latency_ms,
                'redis_version': server_info.get('redis_version'),
                'uptime_days': server_info.get('uptime_in_days'),
                'used_memory': memory_info.get('used_memory_human'),
                'peak_memory': memory_info.get('used_memory_peak_human'),
                'connected_clients': clients_info.get('connected_clients'),
                'read_write_test': 'pass' if probe_ok else 'fail',
                'cache_stats': self.cache_stats,
            }

            if not probe_ok:
                result['status'] = 'degraded'
                logger.warning("Redis health check: read/write probe failed")

            return result
        except Exception as e:
            logger.exception("Redis health check failed")
            return {
                'status': 'unhealthy',
                'backend': 'redis',
                'error': str(e),
                'cache_stats': self.cache_stats,
            }


# Global cache instance
_cache_service = None

def get_cache_service() -> CacheService:
    """Get the global cache service instance."""
    global _cache_service
    if _cache_service is None:
        _cache_service = CacheService()
    return _cache_service


def cache_result(key_prefix: str, ttl: int = 3600, use_args: bool = True):
    """
    Decorator to cache function results.
    
    Args:
        key_prefix: Prefix for cache key
        ttl: Time to live in seconds
        use_args: Whether to include function arguments in cache key
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache = get_cache_service()
            
            # Generate cache key
            if use_args:
                # Create hash of arguments for cache key
                args_str = str(args) + str(sorted(kwargs.items()))
                args_hash = hashlib.md5(args_str.encode()).hexdigest()[:8]
                cache_key = f"{key_prefix}:{func.__name__}:{args_hash}"
            else:
                cache_key = f"{key_prefix}:{func.__name__}"
            
            # Try to get from cache
            cached_result = cache.get(cache_key)
            if cached_result is not None:
                logger.debug(f"Cache hit for {cache_key}")
                return cached_result
            
            # Execute function and cache result
            result = func(*args, **kwargs)
            cache.set(cache_key, result, ttl)
            logger.debug(f"Cache set for {cache_key}")
            
            return result
        return wrapper
    return decorator


# Specific cache decorators for common use cases
def cache_analysis_result(ttl: int = 1800):  # 30 minutes
    """Cache analysis results."""
    return cache_result("analysis", ttl=ttl)

def cache_user_data(ttl: int = 600):  # 10 minutes
    """Cache user data."""
    return cache_result("user", ttl=ttl)

def cache_project_data(ttl: int = 900):  # 15 minutes
    """Cache project data."""
    return cache_result("project", ttl=ttl)
