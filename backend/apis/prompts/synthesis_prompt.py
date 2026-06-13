"""
Synthesis Prompt Template for GPT-5-mini

Single prompt template for synthesizing insights, feature descriptions, and work items
from structured pipeline results.
"""

def create_synthesis_prompt(structured_extractions: dict, aggregated_stats: dict, 
                          representative_samples: list, company_name: str = "Company") -> str:
    """
    Create the synthesis prompt for GPT-5-mini.
    
    Args:
        structured_extractions: Per-comment extractions with aspects, sentiment, confidence
        aggregated_stats: Aggregated statistics per aspect
        representative_samples: List of representative comment texts
        company_name: Company name for context
        
    Returns:
        Formatted prompt string for GPT-5-mini
    """
    
    total_comments = structured_extractions.get('total_comments',
                        len(structured_extractions.get('per_comment_extractions', [])))

    prompt = f"""
You are an expert customer feedback analyst. Analyze the following structured customer feedback data for {company_name} and provide actionable insights.

## OVERVIEW
- Total Comments Analyzed: {total_comments}
- Processing Method: Local ML Pipeline (all-MiniLM-L6-v2 + cardiffnlp/twitter-roberta-base-sentiment-latest)
- Sentiment Method: Aspect-relative (sentence-level sentiment per matched aspect)
- Aspect Matching: Bi-encoder cosine similarity with tiered thresholds (0.60 strong / 0.55 weak)

## AGGREGATED STATISTICS
Overall Sentiment Distribution:
{_format_overall_sentiment(aggregated_stats.get('overall_sentiment', {}))}

Confidence Distribution:
{_format_confidence_distribution(aggregated_stats.get('confidence_distribution', {}))}

Unmapped Comments: {aggregated_stats.get('unmapped_percentage', 0):.1%} ({aggregated_stats.get('unmapped_count', 0)} comments)

## ASPECT-LEVEL ANALYSIS
{_format_aspect_analysis(aggregated_stats)}

## REPRESENTATIVE SAMPLE COMMENTS
The following are {len(representative_samples)} representative comments selected proportionally across sentiments:

{_format_sample_comments(representative_samples)}

## TASK
Based on this comprehensive analysis, provide a JSON response with exactly this structure:

```json
{{
    "insights": [
        "Key insight 1 about customer sentiment patterns",
        "Key insight 2 about specific issues or praise",
        "Key insight 3 about trends or recommendations",
        "Key insight 4 (optional)",
        "Key insight 5 (optional)"
    ],
    "features": [
        {{
            "feature": "Feature/Aspect Name",
            "description": "Brief description of customer sentiment about this feature",
            "sentiment": {{
                "positive": 65.0,
                "negative": 20.0,
                "neutral": 15.0
            }},
            "keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"],
            "comment_count": 34
        }}
    ],
    "work_items": [
        {{
            "title": "Actionable Work Item Title",
            "description": "Detailed description of what needs to be done",
            "priority": "High|Medium|Low",
            "type": "Bug Fix|Feature Enhancement|Investigation|Process Improvement",
            "affected_feature": "Feature name if applicable",
            "estimated_impact": "Description of expected impact"
        }}
    ]
}}
```

## REQUIREMENTS
1. **Insights**: 3-5 key insights focusing on actionable patterns, not just statistics
2. **Features**: Include ALL aspects with significant comment volume (>5% of total)
3. **Work Items**: Generate specific, actionable items based on negative sentiment and improvement opportunities
4. **Keywords**: Use actual words from customer comments, not generic terms
5. **Priorities**: High for critical issues affecting many customers, Medium for improvements, Low for minor enhancements

Focus on actionable intelligence that can drive product and service improvements.
"""
    
    return prompt


def _format_overall_sentiment(overall_sentiment: dict) -> str:
    """Format overall sentiment distribution."""
    if not overall_sentiment:
        return "No sentiment data available"
    
    lines = []
    for sentiment, percentage in overall_sentiment.items():
        lines.append(f"- {sentiment.title()}: {percentage:.1f}%")
    
    return "\n".join(lines)


def _format_confidence_distribution(confidence_dist: dict) -> str:
    """Format confidence distribution."""
    if not confidence_dist:
        return "No confidence data available"
    
    total = sum(confidence_dist.values())
    if total == 0:
        return "No confidence data available"
    
    lines = []
    for confidence, count in confidence_dist.items():
        percentage = (count / total) * 100
        lines.append(f"- {confidence}: {percentage:.1f}% ({count} comments)")
    
    return "\n".join(lines)


def _format_aspect_analysis(aggregated_stats: dict) -> str:
    """Format detailed aspect-level analysis."""
    aspect_sentiment_counts = aggregated_stats.get('aspect_sentiment_counts', {})
    aspect_keywords = aggregated_stats.get('aspect_keywords', {})
    
    if not aspect_sentiment_counts:
        return "No aspect data available"
    
    lines = []
    for aspect, sentiment_counts in aspect_sentiment_counts.items():
        if aspect == "UNMAPPED":
            continue
            
        total = sum(sentiment_counts.values())
        if total == 0:
            continue
        
        # Calculate percentages
        sentiment_pcts = {}
        for sentiment, count in sentiment_counts.items():
            sentiment_pcts[sentiment.lower()] = (count / total) * 100
        
        # Get keywords
        keywords = aspect_keywords.get(aspect, [])[:5]
        keyword_str = ", ".join(keywords) if keywords else "No keywords"
        
        # Format sentiment breakdown
        sentiment_breakdown = []
        for sentiment in ['positive', 'negative', 'neutral', 'mixed']:
            if sentiment.upper() in sentiment_counts:
                pct = sentiment_pcts.get(sentiment, 0)
                sentiment_breakdown.append(f"{sentiment}: {pct:.1f}%")
        
        lines.append(f"""
**{aspect}** ({total} comments)
- Sentiment: {' | '.join(sentiment_breakdown)}
- Keywords: {keyword_str}""")
    
    return "\n".join(lines)


def _format_sample_comments(representative_samples: list) -> str:
    """Format representative sample comments."""
    if not representative_samples:
        return "No sample comments available"
    
    lines = []
    for i, comment in enumerate(representative_samples[:15], 1):  # Limit to 15 for prompt length
        # Truncate very long comments
        display_comment = comment[:200] + "..." if len(comment) > 200 else comment
        lines.append(f"{i}. \"{display_comment}\"")
    
    if len(representative_samples) > 15:
        lines.append(f"... and {len(representative_samples) - 15} more comments")
    
    return "\n".join(lines)