import re
import json
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("apis.app")

def flatten_feedback(data: List[Dict[str, Any]]) -> List[str]:
    """
    Extract feedback text from data structures.
    
    Args:
        data: List of feedback entries with "Feedback" key
        
    Returns:
        List of feedback text strings
    """
    if not data or not isinstance(data, list):
        return []
    
    feedback_texts = []
    for entry in data:
        if isinstance(entry, dict) and "Feedback" in entry:
            feedback_texts.append(str(entry["Feedback"]))
        elif isinstance(entry, str):
            feedback_texts.append(entry)
    
    return feedback_texts

def fix_json_string(faulty_json: str) -> str:
    """
    Clean and fix malformed JSON strings from LLM responses.
    Enhanced to handle control characters, arrays, and objects.
    
    Args:
        faulty_json: Potentially malformed JSON string (can be array [] or object {})
        
    Returns:
        Cleaned JSON string (original format preserved - array or object)
        
    Raises:
        ValueError: If input is empty or None
    """
    if not faulty_json:
        raise ValueError("Input JSON string cannot be empty")
    
    # Step 0: Try direct parsing first - if it works, return as-is (no aggressive cleaning needed)
    try:
        json.loads(faulty_json)
        logger.debug("JSON validated without cleaning")
        return faulty_json
    except json.JSONDecodeError:
        pass  # Continue with cleaning
    
    try:
        # Step 1: Remove markdown code blocks if present (do this early to preserve structure)
        cleaned_json = faulty_json.strip()
        if cleaned_json.startswith("```json"):
            cleaned_json = cleaned_json[7:]
        elif cleaned_json.startswith("```"):
            cleaned_json = cleaned_json[3:]
        if cleaned_json.endswith("```"):
            cleaned_json = cleaned_json[:-3]
        cleaned_json = cleaned_json.strip()
        
        # Step 2: Remove or escape control characters that break JSON parsing
        # Remove control characters (0x00-0x1F) except allowed ones (tab, newline, carriage return)
        cleaned_json = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', cleaned_json)
        
        # Step 3: Extract JSON array or object from text if embedded
        # Try to find JSON structure (array [] or object {})
        json_start = -1
        json_end = -1
        
        # Check for array first (our use case)
        array_start = cleaned_json.find('[')
        object_start = cleaned_json.find('{')
        
        if array_start != -1 and (object_start == -1 or array_start < object_start):
            # Array found, extract it
            json_start = array_start
            # Find matching closing bracket
            bracket_count = 0
            for i in range(json_start, len(cleaned_json)):
                if cleaned_json[i] == '[':
                    bracket_count += 1
                elif cleaned_json[i] == ']':
                    bracket_count -= 1
                    if bracket_count == 0:
                        json_end = i + 1
                        break
            if json_end > json_start:
                cleaned_json = cleaned_json[json_start:json_end]
        elif object_start != -1:
            # Object found, extract it
            json_start = object_start
            # Find matching closing brace
            brace_count = 0
            for i in range(json_start, len(cleaned_json)):
                if cleaned_json[i] == '{':
                    brace_count += 1
                elif cleaned_json[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        json_end = i + 1
                        break
            if json_end > json_start:
                cleaned_json = cleaned_json[json_start:json_end]
        
        # Step 4: Fix common escape sequence issues (but preserve structure)
        # Only fix actual escape sequences, don't break valid JSON
        cleaned_json = cleaned_json.replace(r'\\n', '\n')  # Unescape newlines in strings
        cleaned_json = re.sub(r'(?<!\\)\\"', '"', cleaned_json)  # Fix unescaped quotes in strings
        
        # Step 5: Fix unterminated strings by finding and closing them
        cleaned_json = fix_unterminated_strings(cleaned_json)
        
        # Step 6: Validate and fix common JSON issues
        cleaned_json = fix_common_json_issues(cleaned_json)
        
        # Step 7: Final validation
        try:
            parsed = json.loads(cleaned_json)
            logger.debug("JSON cleaned and validated")
            # Return the cleaned JSON string
            return cleaned_json
        except json.JSONDecodeError as e:
            # Last resort: try to extract JSON using extract_json_from_text
            logger.warning("JSON validation failed after cleaning; attempting extraction")
            extracted = extract_json_from_text(faulty_json)
            if extracted:
                try:
                    json.loads(extracted)
                    logger.debug("JSON extracted from text")
                    return extracted
                except json.JSONDecodeError:
                    pass
            
            # If all else fails, return the cleaned version so the validator can handle it
            logger.error("Failed to parse JSON after all cleaning attempts")
            # Don't return an error dict - return the cleaned JSON so validator can handle it
            return cleaned_json
        
    except Exception:
        logger.exception("Failed to fix JSON string")
        # Return original on exception - let validator handle it
        return faulty_json

def fix_unterminated_strings(json_str: str) -> str:
    """
    Fix unterminated strings in JSON by properly closing them.
    
    Args:
        json_str: JSON string with potential unterminated strings
        
    Returns:
        JSON string with fixed string termination
    """
    try:
        # Simple approach: ensure all quotes are properly paired
        # This is a basic implementation - more sophisticated parsing could be added
        
        # Count quotes and fix obvious issues
        quote_count = json_str.count('"')
        if quote_count % 2 != 0:
            # Odd number of quotes - likely unterminated string
            # Find the last quote and see if it needs closing
            last_quote_pos = json_str.rfind('"')
            if last_quote_pos != -1:
                # Check if this quote is properly closed
                remaining = json_str[last_quote_pos + 1:]
                if not any(c in remaining for c in ['"', '}', ']', ',']):
                    # Likely unterminated - add closing quote
                    json_str = json_str[:last_quote_pos + 1] + '"' + remaining
        
        return json_str
    except Exception:
        logger.exception("Failed to fix unterminated strings")
        return json_str

def fix_common_json_issues(json_str: str) -> str:
    """
    Fix common JSON formatting issues.
    
    Args:
        json_str: JSON string to fix
        
    Returns:
        Fixed JSON string
    """
    try:
        # Fix trailing commas
        json_str = re.sub(r',\s*}', '}', json_str)
        json_str = re.sub(r',\s*]', ']', json_str)
        
        # Fix missing commas between objects/arrays
        json_str = re.sub(r'}\s*{', '},{', json_str)
        json_str = re.sub(r']\s*\[', '],[', json_str)
        
        # Fix unquoted keys (basic cases)
        json_str = re.sub(r'(\w+):', r'"\1":', json_str)
        
        # Fix single quotes to double quotes
        json_str = re.sub(r"'([^']*)'", r'"\1"', json_str)
        
        return json_str
    except Exception:
        logger.exception("Failed to fix common JSON issues")
        return json_str

def validate_json_structure(cleaned_json: str, validation_type: int = 0) -> Optional[Dict[str, Any]]:
    """
    Validate the structure of the JSON after parsing.
    
    Args:
        cleaned_json: JSON string to validate
        validation_type: 0 for sentiment analysis, 1 for deep analysis
        
    Returns:
        Parsed JSON if valid, None if invalid
    """
    try:
        parsed_json = json.loads(cleaned_json)
        
        # Define the expected schema structures
        sentiment_required_fields = [
            "sentiment_summary", "features", "emoji_analysis", 
            "positive_keywords", "negative_keywords"
        ]
        deep_analysis_required_fields = [
            "negative_summary", "action_items"
        ]
        
        required_fields = sentiment_required_fields if validation_type == 0 else deep_analysis_required_fields
        
        # Validate root-level fields (accept legacy 'feature_asba' as alias for 'features')
        missing_fields = [
            field for field in required_fields
            if field not in parsed_json and not (field == "features" and ("feature_asba" in parsed_json or "featureasba" in parsed_json))
        ]
        if missing_fields:
            logger.warning("Missing required root-level fields", extra={"missing_fields": missing_fields})
            return None

        # Validate sentiment_summary structure (for sentiment analysis)
        if validation_type == 0 and "sentiment_summary" in parsed_json:
            sentiment_summary = parsed_json["sentiment_summary"]
            required_sentiment_keys = ["positive", "negative", "neutral"]
            if not all(isinstance(sentiment_summary.get(k), str) for k in required_sentiment_keys):
                logger.warning("Invalid sentiment summary structure")
                return None

            # Validate features structure (with fallback to legacy key)
            features_data = parsed_json.get("features") or parsed_json.get("feature_asba")
            if features_data:
                for i, feature in enumerate(features_data):
                    if not isinstance(feature, dict):
                        continue
                    required_feature_keys = ["feature", "sentiment", "keywords", "explanations"]
                    if not all(k in feature for k in required_feature_keys):
                        logger.warning("Missing keys in feature entry", extra={"feature_index": i})
                        continue

                    feature_sentiment = feature.get("sentiment", {})
                    if not all(isinstance(feature_sentiment.get(k), str) for k in required_sentiment_keys):
                        logger.warning("Invalid sentiment structure for feature", extra={"feature_index": i})
                        continue

            # Validate emoji_analysis
            if "emoji_analysis" in parsed_json:
                emoji_analysis = parsed_json["emoji_analysis"]
                if not (isinstance(emoji_analysis.get("top_emojis"), list) and 
                        isinstance(emoji_analysis.get("overall_sentiment"), str)):
                    logger.warning("Invalid emoji analysis structure")
                    return None

        # Validate action_items (for both types)
        if "action_items" in parsed_json:
            action_items = parsed_json["action_items"]
            required_action_categories = ["feature_requests", "bugs", "change_requests"]
            if not all(k in action_items and isinstance(action_items[k], list) 
                      for k in required_action_categories):
                logger.warning("Invalid action items structure")
                return None

        # If all checks pass
        logger.debug("JSON structure validation passed")
        return parsed_json

    except json.JSONDecodeError:
        logger.exception("Failed to parse JSON during validation")
        return None
    except Exception:
        logger.exception("Failed to validate JSON structure")
        return None

def sanitize_llm_output(output: str, max_length: int = 10000) -> str:
    """
    Sanitize LLM output for security and length.
    
    Args:
        output: Raw LLM output
        max_length: Maximum allowed length
        
    Returns:
        Sanitized output string
    """
    if not output:
        return ""
    
    # Truncate if too long
    if len(output) > max_length:
        output = output[:max_length] + "... [truncated]"
        logger.warning(f"LLM output truncated to {max_length} characters")
    
    # Remove potentially dangerous content
    # Remove script tags and other potentially harmful content
    output = re.sub(r'<script[^>]*>.*?</script>', '', output, flags=re.IGNORECASE | re.DOTALL)
    output = re.sub(r'<iframe[^>]*>.*?</iframe>', '', output, flags=re.IGNORECASE | re.DOTALL)
    
    return output.strip()

def extract_json_from_text(text: str) -> Optional[str]:
    """
    Extract JSON content from mixed text that might contain JSON.
    Handles both arrays [] and objects {}.
    
    Args:
        text: Text that might contain JSON
        
    Returns:
        Extracted JSON string or None if not found
    """
    if not text:
        return None
    
    # Try to find JSON arrays first (our primary use case)
    bracket_count = 0
    array_start = text.find('[')
    if array_start != -1:
        for i in range(array_start, len(text)):
            if text[i] == '[':
                bracket_count += 1
            elif text[i] == ']':
                bracket_count -= 1
                if bracket_count == 0:
                    array_json = text[array_start:i+1]
                    try:
                        json.loads(array_json)
                        return array_json
                    except json.JSONDecodeError:
                        break
    
    # Try to find JSON objects
    brace_count = 0
    object_start = text.find('{')
    if object_start != -1:
        for i in range(object_start, len(text)):
            if text[i] == '{':
                brace_count += 1
            elif text[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    object_json = text[object_start:i+1]
                    try:
                        json.loads(object_json)
                        return object_json
                    except json.JSONDecodeError:
                        break
    
    # Fallback: try regex pattern (less reliable)
    json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}|\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\]'
    matches = re.findall(json_pattern, text, re.DOTALL)
    
    for match in matches:
        try:
            # Test if it's valid JSON
            json.loads(match)
            return match
        except json.JSONDecodeError:
            continue
    
    return None