import os
import uuid
import time
import logging
import ipaddress
from django.utils.deprecation import MiddlewareMixin
from opentelemetry import trace
from ..core.request_context import request_id_var, token_usage_var

logger = logging.getLogger(__name__)

class AzureHostValidationMiddleware(MiddlewareMixin):
    """
    Middleware to handle Azure App Service internal health check IPs.
    
    Azure health checks come from internal IPs (169.254.x.x) which aren't in ALLOWED_HOSTS.
    This middleware fixes the HTTP_HOST header for Azure internal requests by using
    the X-Forwarded-Host header or the WEBSITE_HOSTNAME environment variable.
    """
    
    def __init__(self, get_response):
        super().__init__(get_response)
        self.get_response = get_response
        self.is_azure = bool(os.environ.get('WEBSITE_INSTANCE_ID'))
        self.website_hostname = os.environ.get('WEBSITE_HOSTNAME', '')
    
    def process_request(self, request):
        """Fix host header for Azure internal health checks"""
        if not self.is_azure:
            return None
        
        # Get the raw host from the request
        raw_host = request.META.get('HTTP_HOST', '')
        if not raw_host:
            return None
        
        host = raw_host.split(':')[0]  # Remove port if present
        
        # Check if host is an Azure internal IP (169.254.0.0/16)
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private and str(ip).startswith('169.254.'):
                # This is an Azure internal health check
                # Try to use X-Forwarded-Host first
                forwarded_host = request.META.get('HTTP_X_FORWARDED_HOST', '')
                if forwarded_host:
                    # Extract hostname from forwarded host (may include port)
                    forwarded_hostname = forwarded_host.split(':')[0].split(',')[0].strip()
                    request.META['HTTP_HOST'] = forwarded_hostname
                    logger.debug(f"Fixed Azure health check host using X-Forwarded-Host: {host} -> {forwarded_hostname}")
                elif self.website_hostname:
                    # Use the site's actual hostname from environment
                    request.META['HTTP_HOST'] = self.website_hostname
                    logger.debug(f"Fixed Azure health check host using WEBSITE_HOSTNAME: {host} -> {self.website_hostname}")
                else:
                    # Fallback to .azurewebsites.net pattern
                    request.META['HTTP_HOST'] = '.azurewebsites.net'
                    logger.debug(f"Fixed Azure health check host using fallback: {host} -> .azurewebsites.net")
        except (ValueError, ipaddress.AddressValueError):
            # Not an IP address, let Django handle it normally
            pass
        
        return None


class RequestResponseLoggingMiddleware(MiddlewareMixin):
    """
    Lightweight middleware for request correlation - OpenTelemetry handles detailed logging
    """
    
    def __init__(self, get_response):
        super().__init__(get_response)
        self.get_response = get_response

    def process_request(self, request):
        """Set up request correlation ID"""
        # Start timing
        request.start_time = time.time()
        
        # Correlation ID: use incoming or generate
        request_id = (
            request.headers.get('X-Request-ID')
            or request.headers.get('X-Correlation-ID')
            or str(uuid.uuid4())
        )
        request.request_id = request_id
        request_id_var.set(request_id)
        
        return None

    def process_response(self, request, response):
        """Add request ID to response headers, enrich OTel span, and clean up context."""
        # Echo X-Request-ID in response for client correlation
        try:
            if hasattr(request, 'request_id') and request.request_id:
                response.headers['X-Request-ID'] = request.request_id
        except Exception:
            pass

        # Enrich the active OTel span with token usage from the request context
        try:
            token_usage = token_usage_var.get()
            if token_usage:
                span = trace.get_current_span()
                if span and span.is_recording():
                    for key in ('user_id', 'project_id', 'task_type', 'model',
                                'input_tokens', 'output_tokens', 'total_tokens',
                                'cost_usd', 'latency_ms'):
                        val = token_usage.get(key)
                        if val is not None:
                            span.set_attribute(f"saramsa.genai.{key}", val)
        except Exception:
            pass

        # Clear contextvars for safety (avoid leaking identity across pooled requests)
        try:
            request_id_var.set(None)
            token_usage_var.set(None)
            from ..core.request_context import user_id_var, organization_id_var
            user_id_var.set(None)
            organization_id_var.set(None)
        except Exception:
            pass

        return response


class PerformanceMonitoringMiddleware(MiddlewareMixin):
    """
    Basic performance monitoring - OpenTelemetry provides detailed metrics
    """
    
    def __init__(self, get_response):
        super().__init__(get_response)
        self.get_response = get_response

    def process_request(self, request):
        """Start performance monitoring"""
        request.perf_start = time.time()
        return None

    def process_response(self, request, response):
        """Log only high duration requests"""
        if hasattr(request, 'perf_start'):
            duration = time.time() - request.perf_start
            
            # Log only requests taking more than 5 seconds
            if duration > 5.0:
                logger.warning(
                    "Slow request",
                    extra={"method": request.method, "path": request.path, "duration_ms": round(duration * 1000, 1)},
                )
        
        return response


class SecurityLoggingMiddleware(MiddlewareMixin):
    """
    Basic security event logging - OpenTelemetry captures detailed request data
    """
    
    def __init__(self, get_response):
        super().__init__(get_response)
        self.get_response = get_response

    def process_request(self, request):
        """Log critical security events only"""
        # Log authentication attempts
        if request.path.endswith('/login/') or request.path.endswith('/register/'):
            logger.info(
                "Authentication attempt",
                extra={"method": request.method, "path": request.path, "client_ip": self._get_client_ip(request)},
            )
        
        return None

    def _get_client_ip(self, request):
        """Extract client IP address from request"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip