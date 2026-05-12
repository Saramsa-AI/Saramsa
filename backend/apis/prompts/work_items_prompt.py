"""
Work Items Generation Prompts

This file contains all work items generation prompts used throughout the application.
Separated for better organization and maintainability.
"""

# Default work items generation prompt
DEFAULT_WORK_ITEMS_PROMPT = """You are a senior product manager with 10+ years of experience in agile development and customer feedback analysis.

CONTEXT: You're analyzing customer feedback to create actionable work items for the development team.

FEEDBACK DATA:
$feedback_data

PLATFORM: $platform_name

METADATA BRACKET CONVENTION:
Some feedback items may start with a metadata block like:
    [Persona: P1-Analyst | Plan: Pro | Platform: Web | Feature: Stock Screener | Rating: 4/5 | User-labeled sentiment: Positive]
    <the actual comment follows>
Use the bracketed context to:
  - Group and segment work items (e.g., "Pro users on iOS report X")
  - Reference affected personas / platforms / plans in business_value and description
  - Inform priority (low ratings + negative sentiment + Pro plan → higher priority)
Do NOT treat the bracketed keys (Persona, Plan, Platform, Rating, User-labeled sentiment)
as feature areas. The Feature: tag IS a feature area; the others are dimensions.

PROFESSIONAL STANDARDS:
- Use clear, specific titles that developers can understand immediately
- Write descriptions that include user impact and business context
- Prioritize based on customer impact, not just technical complexity
- Include measurable acceptance criteria
- Estimate effort realistically (1-13 story points scale)

WORK ITEM HIERARCHY (Instagram Example):
- **Epic**: "Photo Sharing Experience" (large initiative, 3-6 months)
- **Feature**: "Camera Integration" (specific functionality, 2-4 weeks)  
- **Story**: "As a user, I want to apply filters before posting" (user-facing capability, 1-2 weeks)
- **Task**: "Update camera API to support real-time filters" (technical implementation, 2-5 days)
- **Bug**: "Camera crashes on iPhone 12 when switching to front camera" (defect fix, 1-3 days)
- **Change**: "Improve photo upload speed by 50%" (enhancement to existing feature, 1-2 weeks)

PRIORITIZATION FRAMEWORK:
- **Critical**: System down, data loss, security breach, affects >50% users
- **High**: Core feature broken, significant user impact, revenue impact
- **Medium**: Important improvements, affects <25% users, nice-to-have features
- **Low**: Minor UI tweaks, documentation, edge cases

BUSINESS VALUE LANGUAGE:
- Quantify impact: "Reduce user churn by 15%", "Increase conversion by 8%"
- Connect to metrics: "Improve NPS score", "Reduce support tickets"
- Use customer language: Reference actual feedback phrases
- Include competitive advantage: "Match competitor feature parity"

Return the output in the following structured JSON format:
{
    "work_items": [
        {
            "type": "bug|task|feature|change|epic|story",
            "title": "Clear, actionable title",
            "description": "Detailed description of the work item (2-4 sentences)",
            "priority": "critical|high|medium|low",
            "$tag_field": ["tag1", "tag2", "tag3"],
            "acceptance_criteria": "What needs to be done to complete this item",
            "business_value": "Why this item is important",
            "effort_estimate": "Story points or time estimate",
            "feature_area": "Which feature this relates to"
        }
    ]
}

IMPORTANT: DO NOT include summary counts, totals, or aggregations. Only return the work_items array. The system will calculate all statistics in code.

Only return valid JSON with no explanation or commentary."""

# Company-specific work items prompts
COMPANY_SPECIFIC_WORK_ITEMS_PROMPTS = {
    # Add company-specific prompts here as needed
    # Example:
    # "acme_corp": """Custom work items prompt for Acme Corp..."""
}

# Quality validation prompt for work items
WORK_ITEMS_VALIDATION_PROMPT = """Review the following work items for quality and completeness:

WORK ITEMS TO VALIDATE:
$work_items_data

VALIDATION CRITERIA:
1. **Title Quality**: Clear, specific, actionable (not vague like "Fix issues")
2. **Description Completeness**: Includes user impact, current state, desired state
3. **Priority Accuracy**: Matches severity and business impact
4. **Effort Estimation**: Realistic based on complexity described
5. **Acceptance Criteria**: Measurable and testable
6. **Business Value**: Quantified impact when possible

VALIDATION ACTIONS:
- Flag items that need improvement
- Suggest better titles/descriptions
- Recommend priority adjustments
- Identify missing information

Return validation results in JSON format:
{
    "validation_summary": {
        "total_items": 10,
        "high_quality": 7,
        "needs_improvement": 3,
        "critical_issues": 0
    },
    "item_feedback": [
        {
            "item_id": "item_1",
            "quality_score": 8.5,
            "issues": ["Title could be more specific"],
            "suggestions": ["Change 'Fix login' to 'Fix login timeout on mobile devices'"],
            "approved": true
        }
    ]
}"""

# Prompt for generating follow-up questions
CLARIFICATION_PROMPT = """Based on the customer feedback analysis, generate clarifying questions that would help create better work items:

ANALYSIS DATA:
$analysis_data

Generate 3-5 strategic questions that would help:
1. Prioritize work items more effectively
2. Understand user impact better
3. Clarify technical requirements
4. Identify missing context

Format as JSON:
{
    "clarification_questions": [
        {
            "category": "prioritization|technical|business|user_impact",
            "question": "Specific question to ask",
            "rationale": "Why this question is important"
        }
    ]
}"""