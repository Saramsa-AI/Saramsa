"""
Views package for integrations.

Organized into logical modules:
- integration_views: Integration account management
- project_views: Project CRUD operations
- external_views: External API interactions
"""

from .integration_views import (
    get_integration_accounts,
    create_azure_integration,
    create_jira_integration,
    create_asana_integration,
    test_integration_connection,
    delete_integration_account,
)
from .project_views import (
    ProjectCreateView,
    ProjectListView,
    ProjectDetailView,
    LatestAnalysisView,
    ProjectTrendsView,
    ProjectAspectTrendView,
    ProjectRolesView,
)
from .external_views import (
    get_azure_projects,
    get_jira_projects,
    get_asana_workspaces,
    get_asana_projects,
    get_dashboard_azure_projects,
    get_dashboard_jira_projects,
    get_dashboard_asana_projects,
    get_external_projects,
    check_external_project,
)
from .slack_views import (
    slack_oauth_start,
    slack_oauth_callback,
    slack_list_channels,
    slack_test_connection,
)
from .source_views import (
    create_feedback_source,
    list_feedback_sources,
    feedback_source_detail,
    feedback_source_sync_now,
)
from .asana_views import (
    configure_asana_target,
    push_insight_to_asana,
)

__all__ = [
    # Integration views
    'get_integration_accounts',
    'create_azure_integration',
    'create_jira_integration',
    'create_asana_integration',
    'test_integration_connection',
    'delete_integration_account',
    # Project views
    'ProjectCreateView',
    'ProjectListView',
    'ProjectDetailView',
    'LatestAnalysisView',
    'ProjectTrendsView',
    'ProjectAspectTrendView',
    'ProjectRolesView',
    # External views
    'get_azure_projects',
    'get_jira_projects',
    'get_asana_workspaces',
    'get_asana_projects',
    'get_dashboard_azure_projects',
    'get_dashboard_jira_projects',
    'get_dashboard_asana_projects',
    'get_external_projects',
    'check_external_project',
    # Slack views
    'slack_oauth_start',
    'slack_oauth_callback',
    'slack_list_channels',
    'slack_test_connection',
    # Source views
    'create_feedback_source',
    'list_feedback_sources',
    'feedback_source_detail',
    'feedback_source_sync_now',
    # Asana views
    'configure_asana_target',
    'push_insight_to_asana',
]
