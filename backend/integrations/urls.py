"""
URL routing for integrations API endpoints.
"""

from django.urls import path
from .views import (
    # Integration views
    get_integration_accounts,
    create_azure_integration,
    create_jira_integration,
    create_asana_integration,
    test_integration_connection,
    delete_integration_account,
    # Project views
    ProjectCreateView,
    ProjectListView,
    ProjectDetailView,
    LatestAnalysisView,
    ProjectTrendsView,
    ProjectAspectTrendView,
    ProjectRolesView,
    # External views
    get_azure_projects,
    get_jira_projects,
    get_asana_workspaces,
    get_asana_projects,
    get_dashboard_azure_projects,
    get_dashboard_jira_projects,
    get_dashboard_asana_projects,
    get_external_projects,
    check_external_project,
    # Slack views
    slack_oauth_start,
    slack_oauth_callback,
    slack_list_channels,
    slack_test_connection,
    # Source views
    create_feedback_source,
    list_feedback_sources,
    feedback_source_detail,
    feedback_source_sync_now,
)

urlpatterns = [
    # Slack OAuth & channel endpoints
    path('slack/oauth/start/', slack_oauth_start, name='slack_oauth_start'),
    path('slack/oauth/callback/', slack_oauth_callback, name='slack_oauth_callback'),
    path('slack/channels/', slack_list_channels, name='slack_list_channels'),
    path('slack/test/', slack_test_connection, name='slack_test_connection'),

    # Feedback source endpoints
    path('sources/create/', create_feedback_source, name='create_feedback_source'),
    path('sources/', list_feedback_sources, name='list_feedback_sources'),
    path('sources/<str:source_id>/sync/', feedback_source_sync_now, name='feedback_source_sync_now'),
    path('sources/<str:source_id>/', feedback_source_detail, name='feedback_source_detail'),

    # External provider project fetching (for configuration) - most specific first
    path('azure/projects/', get_azure_projects, name='get_azure_projects'),
    path('jira/projects/', get_jira_projects, name='get_jira_projects'),
    path('asana/workspaces/', get_asana_workspaces, name='get_asana_workspaces'),
    path('asana/projects/', get_asana_projects, name='get_asana_projects'),

    # Dashboard endpoints (fetch imported projects from database)
    path('dashboard/azure/projects/', get_dashboard_azure_projects, name='get_dashboard_azure_projects'),
    path('dashboard/jira/projects/', get_dashboard_jira_projects, name='get_dashboard_jira_projects'),
    path('dashboard/asana/projects/', get_dashboard_asana_projects, name='get_dashboard_asana_projects'),
    
    # External project utilities
    path('external/projects/check/', check_external_project, name='check_external_project'),
    path('external/projects/', get_external_projects, name='get_external_projects'),
    
    # Project CRUD operations - MOST SPECIFIC FIRST
    path('projects/create/', ProjectCreateView.as_view(), name='project_create_with_path'),
    path('projects/list/', ProjectListView.as_view(), name='project_list'),
    path('projects/<str:project_id>/analysis/latest/', LatestAnalysisView.as_view(), name='project_latest_analysis'),
    path('projects/<str:project_id>/trends/', ProjectTrendsView.as_view(), name='project_trends'),
    path('projects/<str:project_id>/trends/aspects/<str:aspect_key>/', ProjectAspectTrendView.as_view(), name='project_aspect_trends'),
    path('projects/<str:project_id>/roles/', ProjectRolesView.as_view(), name='project_roles'),
    path('projects/<str:project_id>/', ProjectDetailView.as_view(), name='project_detail'),
    path('projects/', ProjectListView.as_view(), name='project_list_root'),  # Changed to list for GET requests
    
    # Integration account management - SPECIFIC ROUTES MUST COME BEFORE PARAMETERIZED ROUTES
    path('azure/', create_azure_integration, name='create_azure_integration'),
    path('jira/', create_jira_integration, name='create_jira_integration'),
    path('asana/', create_asana_integration, name='create_asana_integration'),
    
    # PARAMETERIZED ROUTES MUST COME LAST
    path('<str:account_id>/test/', test_integration_connection, name='test_integration_connection'),
    path('<str:account_id>/', delete_integration_account, name='delete_integration_account'),
    
    # Root path
    path('', get_integration_accounts, name='get_integration_accounts'),
]
