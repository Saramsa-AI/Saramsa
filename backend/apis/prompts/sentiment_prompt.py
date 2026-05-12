"""
Sentiment Analysis Prompts

This file contains all sentiment analysis prompts used throughout the application.
Separated for better organization and maintainability.

Uses LOCKED SEMANTIC SCHEMA - all outputs must match exact schema definition.
"""

# Import locked schema
from feedback_analysis.schemas import SCHEMA_DESCRIPTION

# Default sentiment analysis prompt
DEFAULT_SENTIMENT_PROMPT = """You are a feedback analysis engine.

Your task is to extract structured semantic meaning from each customer comment.

You MUST follow these rules:
- Process each comment independently.
- Return exactly one output object per input comment.
- Do NOT compute totals, percentages, or summaries.
- Do NOT invent new aspects.
- Only use aspects from the provided list.
- If a comment does not clearly map to an aspect, return an empty aspects array.
- Be precise and conservative.

METADATA BRACKET CONVENTION
Some comments start with a metadata block of the form:

  [Persona: ... | Plan: ... | Platform: ... | Feature: <name> | Rating: N/5 | User-labeled sentiment: ...]
  <the actual comment text follows on the next line>

The bracket carries structured context the customer attached to that row. Use
it as follows:

- If a "Feature: <name>" tag is present AND <name> matches one of the allowed
  aspects (case-insensitive), include that aspect in the output for that
  comment. The metadata is gold-label context the customer already provided —
  trust it. Use the prose to identify *additional* aspects beyond the labeled one.
- "User-labeled sentiment" is a strong hint about the overall sentiment of the
  comment. Treat it as a prior, but if the prose clearly contradicts it, the
  prose wins.
- "Rating: N/5" is another sentiment hint (1–2 negative, 3 neutral, 4–5 positive).
  Combine with text, do not blindly echo.
- Do NOT extract Persona, Plan, Platform, or Rating values as aspects. Those
  are dimension metadata, not content aspects.

If a comment has no bracket prefix, treat the whole text as prose and extract
aspects from the text alone.

Allowed values:
- sentiment: POSITIVE | NEGATIVE | NEUTRAL
- confidence: HIGH | MEDIUM | LOW
- intent_type: PRAISE | COMPLAINT | SUGGESTION | OBSERVATION

Aspects to use:
{aspects}

Comments:
{comments}

Return ONLY valid JSON in the following format:

[
  {{
    "comment_id": number,
    "sentiment": "POSITIVE | NEGATIVE | NEUTRAL",
    "confidence": "HIGH | MEDIUM | LOW",
    "intent_type": "PRAISE | COMPLAINT | SUGGESTION | OBSERVATION",
    "intent_phrase": "short neutral phrase describing what the user is talking about",
    "aspects": ["aspect_1", "aspect_2"],
    "keywords": ["keyword1", "keyword2"]
  }}
]"""

# Company-specific sentiment prompts
COMPANY_SPECIFIC_SENTIMENT_PROMPTS = {
    # Add company-specific prompts here as needed
    # Example:
    # "acme_corp": """Custom sentiment prompt for Acme Corp..."""
}

# Context-aware sentiment analysis for different industries
INDUSTRY_SPECIFIC_PROMPTS = {
    "saas": """
ADDITIONAL CONTEXT FOR SAAS PRODUCTS:
- Focus on: Uptime, Performance, User Experience, Integration capabilities
- Common pain points: Login issues, slow loading, feature complexity, billing problems
- Success indicators: User adoption, feature usage, retention metrics
- Sarcasm patterns: "Love paying for features that don't work" = NEGATIVE
""",
    
    "ecommerce": """
ADDITIONAL CONTEXT FOR ECOMMERCE:
- Focus on: Product quality, Shipping, Customer service, Website usability, Payment process
- Common pain points: Delivery delays, product mismatch, checkout issues, return process
- Success indicators: Purchase completion, repeat customers, recommendation likelihood
- Sarcasm patterns: "Great, another broken item" = NEGATIVE
""",
    
    "mobile_app": """
ADDITIONAL CONTEXT FOR MOBILE APPS:
- Focus on: App performance, UI/UX, Battery usage, Crash frequency, Feature availability
- Common pain points: App crashes, slow performance, confusing navigation, missing features
- Success indicators: App store ratings, daily active users, session duration
- Sarcasm patterns: "Amazing how it crashes every time I need it" = NEGATIVE
"""
}

# Enhanced sentiment prompt with confidence scoring
CONFIDENCE_AWARE_SENTIMENT_PROMPT = """You are an expert sentiment analyst with advanced training in detecting nuanced emotions and sarcasm.

FEEDBACK DATA:
$feedback_data

ENHANCED ANALYSIS REQUIREMENTS:

1. **CONFIDENCE SCORING**: Use HIGH/MEDIUM/LOW (exact enum values)
   - HIGH: Very confident (clear positive/negative language)
   - MEDIUM: Confident (mostly clear with minor ambiguity)
   - LOW: Low confidence (highly ambiguous or contradictory)

2. **SARCASM DETECTION PATTERNS**:
   - Positive words with negative context: "Great job" + complaint = NEGATIVE
   - Extreme language with low ratings: "Absolutely perfect" + 1-star = NEGATIVE  
   - Contradictory statements: "Love it" followed by detailed complaints = NEGATIVE
   - Backhanded compliments: "At least it's consistent... consistently bad" = NEGATIVE

3. **CONTEXTUAL SENTIMENT**:
   - Consider rating alongside text
   - Look for emotional indicators: frustration, disappointment, excitement
   - Identify constructive vs destructive criticism
   - Recognize feature-specific sentiment variations

LOCKED OUTPUT SCHEMA:
""" + SCHEMA_DESCRIPTION + """

Return JSON array matching the EXACT schema above with confidence scores."""
