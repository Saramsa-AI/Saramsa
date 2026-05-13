"""
Aspect Suggestion Service

Generates high-level aspect categories from a sample of customer comments.
This is a one-time suggestion pass that runs before the main analysis.

Step 2 of the workflow:
- Samples 50-100 comments from uploaded file
- Uses constrained prompt to identify domain and suggest aspects
- Returns 6-12 high-level aspect categories
"""

import logging
import random
from typing import List, Dict, Any, Optional
import json

from aiCore.services.completion_service import generate_completions

logger = logging.getLogger(__name__)


class AspectSuggestionService:
    """Service for generating aspect suggestions from comment samples."""
    
    def __init__(self):
        self.sample_size_min = 50
        self.sample_size_max = 100
        self.target_aspect_count = 8  # Target 6-12, aim for middle
    
    async def suggest_aspects(self, comments: List[str], company_name: Optional[str] = None,
                             user_id: Optional[str] = None, project_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Generate aspect suggestions from a sample of comments.
        
        Args:
            comments: Full list of comments
            company_name: Optional company name for context
            
        Returns:
            Dictionary with:
            {
                "identified_domain": str,
                "suggested_aspects": List[str],
                "sample_size": int,
                "total_comments": int
            }
        """
        if not comments:
            raise ValueError("Comments list cannot be empty")
        
        # Step 1: Sample comments (50-100)
        sample_comments = self._sample_comments(comments)
        logger.info(
            f"Sampled {len(sample_comments)} comments from {len(comments)} total "
            f"for aspect suggestion"
        )
        
        # Step 2: Build prompt with sample
        prompt = self._build_aspect_suggestion_prompt(sample_comments)
        
        # Step 3: Call LLM with constrained prompt
        result, _usage = await generate_completions(
            prompt, user_id=user_id, project_id=project_id, task_type="aspect_suggestion",
        )
        parsed_result = self._parse_llm_response(result)

        # Step 4: Validate and normalize response
        validated_result = self._validate_aspect_suggestions(parsed_result)

        logger.info(
            f"✅ Aspect suggestion completed: domain='{validated_result['identified_domain']}', "
            f"aspects={len(validated_result['suggested_aspects'])}"
        )

        # Add metadata
        validated_result['sample_size'] = len(sample_comments)
        validated_result['total_comments'] = len(comments)

        return validated_result
    
    def _sample_comments(self, comments: List[str]) -> List[str]:
        """
        Sample comments for aspect suggestion.
        
        Samples 50-100 comments, or all comments if less than 50.
        
        Args:
            comments: Full list of comments
            
        Returns:
            Sampled list of comments
        """
        total = len(comments)
        
        if total <= self.sample_size_min:
            # Use all comments if we have fewer than minimum
            return comments
        
        # Sample between min and max, but don't exceed total
        target_size = min(self.sample_size_max, max(self.sample_size_min, total // 3))
        
        # Use random sampling for variety
        if target_size >= total:
            return comments
        
        sampled = random.sample(comments, target_size)
        return sampled
    
    def _build_aspect_suggestion_prompt(self, sample_comments: List[str]) -> str:
        """
        Build the aspect suggestion prompt.
        
        Uses the highly constrained prompt to identify domain and suggest aspects.
        
        Args:
            sample_comments: Sampled comments for analysis
            
        Returns:
            Formatted prompt string
        """
        # Format comments with clear numbering
        formatted_comments = []
        for i, comment in enumerate(sample_comments):
            formatted_comments.append(f"{i+1}. {comment}")
        
        comments_text = "\n".join(formatted_comments)
        
        prompt = """You are an expert product analyst and domain specialist.

Your task is to analyze the following customer comments and do TWO things:
1) Identify the most appropriate industry or service domain the feedback belongs to.
2) Generate a concise, actionable list of high-level feedback categories ("aspects") suitable for analyzing feedback in that domain.

IMPORTANT RULES:
- The industry/domain must be a clear, commonly understood category (e.g., Hospitality, SaaS, E-commerce, Fintech, Logistics, Healthcare, Education, etc.).
- Aspects must represent broad, reusable experience or responsibility areas within that industry.
- Each aspect should be something a team or function could reasonably own or improve.
- Use short noun or noun-phrase labels only.
- Do NOT generate aspects that are:
  - Too generic (e.g., "experience", "overall", "service quality")
  - Too specific or issue-level (e.g., "AC not working", "login button broken")
  - Full sentences or complaints
- Generate 12-15 aspects to ensure comprehensive coverage of all feedback dimensions.
- You are NOT analyzing sentiment.
- You are NOT summarizing feedback.
- You are ONLY identifying the domain and proposing grouping dimensions.
- These aspects are suggestions and will be reviewed and approved by a user before analysis begins.

Customer comments:
{comments}

Return ONLY valid JSON in the following format:

{{
  "identified_domain": "industry_or_service_domain",
  "suggested_aspects": [
    "aspect_1",
    "aspect_2",
    "aspect_3"
  ]
}}
""".format(comments=comments_text)
        
        return prompt
    
    def _parse_llm_response(self, llm_output: Any) -> Dict[str, Any]:
        """
        Parse LLM response into structured format.
        
        Args:
            llm_output: Raw LLM output (string or dict)
            
        Returns:
            Parsed dictionary with identified_domain and suggested_aspects
        """
        # Handle string output
        if isinstance(llm_output, str):
            # Try to extract JSON from string
            try:
                # Remove markdown code blocks if present
                cleaned = llm_output.strip()
                if cleaned.startswith("```json"):
                    cleaned = cleaned[7:]
                elif cleaned.startswith("```"):
                    cleaned = cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()
                
                parsed = json.loads(cleaned)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse LLM response as JSON: {e}")
                logger.error(f"Response was: {llm_output[:500]}")
                raise ValueError(f"Invalid JSON response from LLM: {str(e)}")
        elif isinstance(llm_output, dict):
            parsed = llm_output
        else:
            raise ValueError(f"Unexpected LLM output type: {type(llm_output)}")
        
        return parsed
    
    def _validate_aspect_suggestions(self, parsed_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate and normalize aspect suggestions.
        
        Args:
            parsed_result: Parsed LLM response
            
        Returns:
            Validated and normalized result
        """
        # Validate structure
        if not isinstance(parsed_result, dict):
            raise ValueError("LLM response must be a dictionary")
        
        # Validate identified_domain
        domain = parsed_result.get("identified_domain")
        if not domain or not isinstance(domain, str):
            logger.warning("Missing or invalid identified_domain, using 'General'")
            domain = "General"
        else:
            domain = domain.strip()
        
        # Validate suggested_aspects
        aspects = parsed_result.get("suggested_aspects", [])
        if not isinstance(aspects, list):
            logger.warning("suggested_aspects is not a list, using empty list")
            aspects = []
        
        # Clean and validate each aspect
        cleaned_aspects = []
        for aspect in aspects:
            if isinstance(aspect, str):
                cleaned = aspect.strip()
                if cleaned and len(cleaned) > 0:
                    # Filter out too generic aspects
                    generic_terms = {"experience", "overall", "service quality", "quality", "service"}
                    if cleaned.lower() not in generic_terms:
                        cleaned_aspects.append(cleaned)
        
        # Limit to 6-12 aspects (target 8)
        if len(cleaned_aspects) > 12:
            logger.warning(f"Too many aspects ({len(cleaned_aspects)}), limiting to 12")
            cleaned_aspects = cleaned_aspects[:12]
        elif len(cleaned_aspects) < 6:
            logger.warning(f"Too few aspects ({len(cleaned_aspects)}), may need review")
        
        if not cleaned_aspects:
            raise ValueError(
                "LLM returned no valid aspects. Ensure Azure OpenAI is configured "
                "and the model can analyse the provided comments."
            )

        return {
            "identified_domain": domain,
            "suggested_aspects": cleaned_aspects
        }


# Global service instance
_aspect_suggestion_service = None

def get_aspect_suggestion_service() -> AspectSuggestionService:
    """Get the global aspect suggestion service instance."""
    global _aspect_suggestion_service
    if _aspect_suggestion_service is None:
        _aspect_suggestion_service = AspectSuggestionService()
    return _aspect_suggestion_service
