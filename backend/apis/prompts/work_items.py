"""
Work Item Generation Prompts

Centralized work item generation prompts. This module provides direct access to 
work item generation prompts without depending on the aiCore prompt system to avoid circular imports.
"""

import logging

from .constants import get_prompt
from .resolver import resolve_prompt_template

logger = logging.getLogger(__name__)


def getDeepAnalysisPrompt(
    platform='azure',
    project_metadata=None,
    company_name: str = None,
    feedback_data: str = None,
    industry: str = None,
    project_id: str = None,
    organization_id: str = None,
):
    """
    Get deep analysis prompt using the enhanced centralized prompt system.
    
    Args:
        platform: 'azure' or 'jira' (default: 'azure')
        project_metadata: Optional dict with project info (for Jira dynamic prompts)
        company_name: Optional company name to get company-specific prompt
        feedback_data: Feedback comments/data to inject into prompt
        industry: Optional industry context for better work item generation
    
    Returns:
        Rendered deep analysis prompt string with variables substituted
    
    Example:
        prompt = getDeepAnalysisPrompt(
            platform='jira',
            project_metadata={'project': {'name': 'MyProject'}},
            feedback_data="User feedback here...",
            industry="saas"
        )
    """
    # Get the enhanced prompt template
    prompt_template = get_prompt(
        company_name=company_name,
        prompt_type="deep_analysis",
        industry=industry
    )

    prompt_template = resolve_prompt_template(
        "deep_analysis",
        prompt_template,
        project_id=project_id,
        organization_id=organization_id,
    )

    # Log original template before substitution
    logger.debug("Deep analysis prompt template (before substitution): %s", prompt_template[:200] + "...")
    
    # Simple variable substitution
    if feedback_data:
        prompt_template = prompt_template.replace("$feedback_data", str(feedback_data))
    
    # Platform-specific substitutions
    platform_name = "Azure DevOps" if platform == 'azure' else "Jira"
    prompt_template = prompt_template.replace("$platform_name", platform_name)
    
    # Handle Jira-specific tag field
    if platform == 'jira':
        prompt_template = prompt_template.replace("$tag_field", "labels")
    else:
        prompt_template = prompt_template.replace("$tag_field", "tags")
    
    # Project metadata substitutions
    if project_metadata and 'project' in project_metadata:
        project_name = project_metadata['project'].get('name', 'Unknown Project')
        prompt_template = prompt_template.replace("$project_name", project_name)
        
        if 'available_issue_type_names' in project_metadata:
            issue_types = ", ".join(project_metadata['available_issue_type_names'])
            prompt_template = prompt_template.replace("$issue_type_list", issue_types)

    # Log final template after all substitutions
    logger.debug("Deep analysis prompt template (after substitution): %s", prompt_template[:200] + "...")
    
    return prompt_template


# Explicit map of allowed work item types per Azure DevOps process template
WORK_ITEM_TYPES_BY_TEMPLATE = {
    "Agile": ["Epic", "Feature", "User Story", "Task", "Bug"],
    "Scrum": ["Epic", "Feature", "Product Backlog Item", "Task", "Bug"],
    "Basic": ["Epic", "Issue", "Task"],
    "CMMI": ["Epic", "Feature", "Requirement", "Task", "Bug"],
}