from rest_framework.views import exception_handler
from rest_framework.exceptions import (
    ValidationError,
    AuthenticationFailed,
    PermissionDenied,
    NotFound,
    NotAuthenticated
)
from .response import StandardResponse
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """
    Custom exception handler that returns RFC 7807 Problem Details format
    """
    # Call REST framework's default exception handler first
    response = exception_handler(exc, context)
    
    # Get request path for instance
    request = context.get('request')
    instance = request.path if request else None
    
    if response is not None:
        # Handle ValidationError with field-level details
        if isinstance(exc, ValidationError):
            errors = []
            if isinstance(exc.detail, dict):
                for field, messages in exc.detail.items():
                    if isinstance(messages, list):
                        for message in messages:
                            errors.append({
                                "field": field,
                                "message": str(message)
                            })
                    else:
                        errors.append({
                            "field": field,
                            "message": str(messages)
                        })
            elif isinstance(exc.detail, list):
                for message in exc.detail:
                    errors.append({"message": str(message)})
            else:
                errors = [{"message": str(exc.detail)}]
            
            return StandardResponse.validation_error(
                detail="One or more fields are invalid.",
                errors=errors if errors else None,
                instance=instance
            )
        
        # Handle authentication errors
        elif isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
            return StandardResponse.unauthorized(
                detail="Authentication credentials were not provided or are invalid.",
                instance=instance
            )
        
        # Handle permission errors
        elif isinstance(exc, PermissionDenied):
            return StandardResponse.forbidden(
                detail="You do not have permission to perform this action.",
                instance=instance
            )
        
        # Handle not found errors
        elif isinstance(exc, NotFound):
            return StandardResponse.not_found(
                detail="The requested resource was not found.",
                instance=instance
            )
        
        # Generic error handling
        else:
            error_message = str(exc.detail) if hasattr(exc, 'detail') else str(exc)
            status_code = response.status_code
            
            return StandardResponse.error(
                title=exc.__class__.__name__.replace('_', ' ').title(),
                detail=error_message,
                status_code=status_code,
                instance=instance
            )
    
    # Log unexpected errors (500s)
    logger.exception("Unhandled exception")
    
    # Include actual error details in DEBUG mode for easier debugging
    if settings.DEBUG:
        error_detail = f"{exc.__class__.__name__}: {str(exc)}"
    else:
        error_detail = "An unexpected error occurred. Please try again later."
    
    return StandardResponse.internal_server_error(
        detail=error_detail,
        instance=request.path if request else None
    )