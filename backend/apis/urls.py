from django.urls import path, include
from django.conf import settings
from .views import health_check, performance_metrics, reset_performance_stats

urlpatterns = [
    # Health and monitoring
    path('api/health/', health_check, name='health-check'),
    path('api/performance/', performance_metrics, name='performance-metrics'),
    path('api/performance/reset/', reset_performance_stats, name='reset-performance-stats'),

    # App routers
    path('api/insights/', include('apis.insights_urls')),
    path('api/feedback/', include('feedback_analysis.urls')),
    path('api/work-items/', include('work_items.urls')),
    path('api/auth/', include('authentication.urls')),
    path('api/integrations/', include('integrations.urls')),
    path('api/billing/', include('billing.urls')),
    path('api/memory/', include('organizational_memory.urls')),
]

if settings.DEBUG or settings.ENABLE_OPENAPI_SCHEMA:
    from drf_spectacular.views import SpectacularAPIView

    urlpatterns += [
        path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    ]

if settings.DEBUG:
    from drf_spectacular.views import SpectacularSwaggerView, SpectacularRedocView

    urlpatterns += [
        path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
        path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    ]
