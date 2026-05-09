"""
DevOps service for work item and integration-related business logic.
This service delegates to the integrations app for external platform operations.
"""

from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
import uuid
import json
import re
from difflib import SequenceMatcher
from ..repositories import WorkItemRepository
from apis.prompts import WORK_ITEM_TYPES_BY_TEMPLATE
from feedback_analysis.services.narration_service import get_narration_service
import logging
from .work_item_candidate_service import get_work_item_candidate_service
from .comment_sampler import sample_comments_for_candidates

logger = logging.getLogger(__name__)


class DevOpsService:
    """Service for DevOps work item business logic."""
    
    def __init__(self):
        self.work_item_repo = WorkItemRepository()
    
    async def generate_work_items_from_analysis(self, analysis_data: Dict[str, Any],
                                              platform: str = "azure",
                                              process_template: str = "Agile",
                                              company_name: str = None,
                                              project_metadata: Dict[str, Any] = None,
                                              comments: List[str] = None,
                                              user_id: str = None) -> Dict[str, Any]:
        """Generate work items from analysis data using deterministic candidates + optional AI narration."""
        try:
            # Phase-2: deterministic candidates from analysis metrics (LLM does not decide existence/priority/type)
            candidate_service = get_work_item_candidate_service()
            candidates = candidate_service.generate_candidates(analysis_data, previous_analysis=None)

            if not candidates:
                return {
                    'success': True,
                    'work_items': [],
                    'summary': self._generate_summary([]),
                    'process_template': process_template,
                    'platform': platform,
                    'generated_at': datetime.now().isoformat(),
                    'raw_llm_response': None
                }

            # Generate work item narration using unified NarrationService (optional)
            # Falls back to deterministic template text if narration fails or budget exceeded

            # Sample relevant comments for each candidate
            comment_samples = {}
            if comments:
                comment_samples = sample_comments_for_candidates(comments, candidates)

            narration_input = {
                "project_id": analysis_data.get("project_id") if isinstance(analysis_data, dict) else None,
                "analysis_id": analysis_data.get("analysis_id") if isinstance(analysis_data, dict) else None,
                "taxonomy_id": analysis_data.get("taxonomy_id") if isinstance(analysis_data, dict) else None,
                "taxonomy_version": analysis_data.get("taxonomy_version") if isinstance(analysis_data, dict) else None,
                "overall": analysis_data.get("overall") if isinstance(analysis_data, dict) else None,
                "features": self._extract_features_for_narration(analysis_data),
                "evidence": [],
                "work_item_candidates": candidates,
                "comment_samples": comment_samples,
            }
            narratives = None
            cached_narration = None
            if isinstance(analysis_data, dict):
                cached_narration = analysis_data.get("narration")
            if isinstance(cached_narration, dict) and cached_narration.get("work_items"):
                narratives = cached_narration
            else:
                try:
                    narration_service = get_narration_service()
                    narratives = narration_service.generate_narratives(narration_input, user_id=user_id)
                except Exception as narration_err:
                    logger.warning(
                        "Narration failed, falling back to deterministic text: %s",
                        narration_err,
                    )
                    narratives = None
            work_items_llm = narratives.get("work_items", []) if isinstance(narratives, dict) else []
            result = narratives

            # Build deterministic work items from candidates
            work_items = self._build_work_items_from_candidates(candidates, analysis_data, process_template)

            # Phase-0/2 guards: LLM must not add/remove/change candidates or priority/type
            self._warn_on_llm_candidate_mutation(candidates, work_items_llm)

            # Apply LLM phrasing to title/description only (never priority/type)
            work_items = self._apply_llm_phrasing(work_items, work_items_llm)

            # Enhanced validation of work items
            work_items = self._validate_and_clean_work_items(work_items, process_template)

            # --- RAG metadata injection (task 6.3) ---
            # If the analysis data contains RAG-enriched candidates, merge their
            # extra metadata (priority_score, why_now, etc.) into the work items.
            rag_enriched = None
            if isinstance(analysis_data, dict):
                rag_enriched = (
                    analysis_data.get("rag_enriched_candidates")
                    or (analysis_data.get("analysisData") or {}).get("rag_enriched_candidates")
                )
            if rag_enriched:
                work_items = self._apply_rag_metadata(work_items, rag_enriched)
            
            # Generate summary in code (not from LLM)
            summary = self._generate_summary(work_items)
            if work_items:
                logger.info("Successfully generated %s work items", len(work_items))

            # Add metadata to each work item
            # CRITICAL: Set analysis_id on each item so they link to the source analysis
            analysis_id_from_context = analysis_data.get("id") or analysis_data.get("analysis_id")
            for item in work_items:
                item["id"] = str(uuid.uuid4())
                item["created_at"] = datetime.now().isoformat()
                item["process_template"] = process_template
                item["platform"] = platform
                if analysis_id_from_context:
                    item["analysis_id"] = str(analysis_id_from_context)

            return {
                'success': True,
                'work_items': work_items,
                'summary': summary,
                'process_template': process_template,
                'platform': platform,
                'generated_at': datetime.now().isoformat(),
                'raw_llm_response': result
            }
            
        except Exception as e:
            logger.error(f"Error generating work items from analysis: {e}")
            raise
    
    def create_work_items(self, user_id: str, work_items: List[Dict[str, Any]],
                         platform: str, project_id: str = None, analysis_id: str = None) -> Dict[str, Any]:
        """Create work items collection with cross-analysis dedup."""
        try:
            # Never use None or the string "None" for IDs (avoids storing workitems_insight_None)
            safe_analysis_id = None
            if analysis_id is not None and str(analysis_id).strip().lower() not in ("", "none"):
                safe_analysis_id = str(analysis_id).strip()

            # --- Set default status on every new work item ---
            for item in work_items:
                item.setdefault("status", "pending")
                item.setdefault("push_status", "not_pushed")
                # CRITICAL: Ensure analysis_id is set on each work item for review queue linkage
                if safe_analysis_id:
                    item["analysis_id"] = safe_analysis_id

            doc_id = f"workitems_{safe_analysis_id}" if safe_analysis_id else str(uuid.uuid4())

            # --- Cross-analysis dedup (create-only path) ---
            # Skip dedup when safe_analysis_id is set: that path uses upsert_by_id which
            # REPLACES all candidates for that story, so there is no accumulation across
            # analyses and deduping against other stories would wrongly wipe every item
            # (same aspect_keys appear in every analysis for the same product).
            if project_id and not safe_analysis_id:
                work_items = self._deduplicate_against_existing(work_items, project_id)
            work_items_doc = {
                "id": doc_id,
                "userId": user_id,
                "platform": platform,
                "analysis_id": safe_analysis_id,
                "work_items": work_items,
                "summary": self._generate_summary(work_items),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "comments_count": len(work_items),
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "updatedAt": datetime.now(timezone.utc).isoformat()
            }

            if project_id:
                work_items_doc["projectId"] = project_id
            if safe_analysis_id and project_id:
                return self.work_item_repo.upsert_by_id(work_items_doc["id"], project_id, work_items_doc)
            return self.work_item_repo.create(work_items_doc)

        except Exception as e:
            logger.error(f"Error creating work items: {e}")
            raise

    def _deduplicate_against_existing(self, new_items: List[Dict[str, Any]],
                                       project_id: str) -> List[Dict[str, Any]]:
        """Remove new work items that already exist in this project (by aspect_key or title).

        Only called on the create (no analysis_id) path. The upsert path skips dedup
        entirely because it replaces all candidates for the story anyway.
        """
        existing = self.work_item_repo.get_all_work_items_flat(project_id)
        if not existing:
            return new_items

        # Build lookup sets from existing items (skip dismissed — they're dead-ended)
        existing_aspect_keys: set = set()
        existing_titles: List[str] = []
        for item in existing:
            status = item.get("status") or "pending"
            if status == "dismissed":
                continue
            ak = item.get("aspect_key")
            if ak:
                existing_aspect_keys.add(str(ak).lower().strip())
            title = item.get("title")
            if title:
                existing_titles.append(self._normalize_title(title))

        kept: List[Dict[str, Any]] = []
        for item in new_items:
            # Check aspect_key match
            ak = item.get("aspect_key")
            if ak and str(ak).lower().strip() in existing_aspect_keys:
                logger.info("Dedup: skipping work item '%s' — aspect_key '%s' already exists in project %s",
                            item.get("title", ""), ak, project_id)
                continue

            # Check title similarity
            new_title = self._normalize_title(item.get("title", ""))
            if new_title and any(self._titles_similar(new_title, et) for et in existing_titles):
                logger.info("Dedup: skipping work item '%s' — similar title already exists in project %s",
                            item.get("title", ""), project_id)
                continue

            kept.append(item)

        skipped = len(new_items) - len(kept)
        if skipped:
            logger.info("Cross-analysis dedup: kept %d, skipped %d duplicates (project=%s)",
                        len(kept), skipped, project_id)
        return kept

    @staticmethod
    def _normalize_title(title: str) -> str:
        """Lowercase, strip punctuation and extra whitespace."""
        title = title.lower().strip()
        title = re.sub(r'[^\w\s]', '', title)
        return re.sub(r'\s+', ' ', title).strip()

    @staticmethod
    def _titles_similar(a: str, b: str) -> bool:
        """Return True if two normalized titles are near-duplicates."""
        if not a or not b:
            return False
        if a == b:
            return True
        if a.startswith(b) or b.startswith(a):
            return True
        return SequenceMatcher(None, a, b).ratio() > 0.85
    
    def get_work_items_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        """Get work items for a project - consolidated method."""
        return self.work_item_repo.get_work_items_by_project(project_id) or []

    def get_work_items_by_analysis_id(self, analysis_id: str) -> Optional[List[Dict[str, Any]]]:
        """Get work items that were generated for a specific analysis."""
        return self.work_item_repo.get_work_items_by_analysis_id(analysis_id)

    def get_work_items_by_user(self, user_id: str) -> List[Dict[str, Any]]:
        """Get work items for a user."""
        return self.work_item_repo.get_by_user(user_id)
    
    def update_work_item(self, work_item_id: str, user_id: str, updated_data: Dict[str, Any], project_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Update embedded work item - consolidated from analysis service."""
        try:
            return self.work_item_repo.update_embedded_work_item(work_item_id, user_id, updated_data, project_id=project_id)
        except Exception as e:
            logger.error(f"Error updating work item {work_item_id}: {e}")
            return None
    
    def remove_work_items(self, work_item_ids: List[str], user_id: str, user_story_id: str = None, project_id: Optional[str] = None) -> Dict[str, Any]:
        """Remove/delete work items by IDs."""
        try:
            removed_count = 0
            failed_ids = []
            
            for work_item_id in work_item_ids:
                try:
                    # Try to remove the work item from the repository
                    success = self.work_item_repo.remove_embedded_work_item(work_item_id, user_id, project_id=project_id)
                    if success:
                        removed_count += 1
                    else:
                        failed_ids.append(work_item_id)
                except Exception as e:
                    logger.error(f"Error removing work item {work_item_id}: {e}")
                    failed_ids.append(work_item_id)
            
            return {
                "success": True,
                "removed_count": removed_count,
                "failed_ids": failed_ids,
                "total_requested": len(work_item_ids),
                "user_story_id": user_story_id
            }
            
        except Exception as e:
            logger.error(f"Error removing work items: {e}")
            raise
    
    def submit_to_external_platform(self, user_id: str, work_items: List[Dict[str, Any]], 
                                   platform: str, project_config: Dict[str, Any]) -> Dict[str, Any]:
        """Submit work items to external platform - delegate to integrations service."""
        try:
            # Import here to avoid circular dependency
            from integrations.services import get_integration_service
            integration_service = get_integration_service()
            
            if platform.lower() == 'azure':
                return self._submit_to_azure_devops(user_id, work_items, project_config, integration_service)
            elif platform.lower() == 'jira':
                return self._submit_to_jira(user_id, work_items, project_config, integration_service)
            else:
                raise ValueError(f"Unsupported platform: {platform}")
                
        except Exception as e:
            logger.error(f"Error submitting to {platform}: {e}")
            raise
    
    def _submit_to_azure_devops(self, user_id: str, work_items: List[Dict[str, Any]], 
                               project_config: Dict[str, Any], integration_service) -> Dict[str, Any]:
        """Submit work items to Azure DevOps via integrations service."""
        try:
            # Get Azure integration account via integrations service
            integration = integration_service.get_integration_account_by_provider(user_id, 'azure')
            if not integration:
                raise ValueError("Azure DevOps integration not found. Please configure integration first.")
            
            # Get decrypted credentials via integrations service
            account_with_creds = integration_service.get_decrypted_credentials(user_id, 'azure')
            if not account_with_creds:
                raise ValueError("Failed to retrieve Azure DevOps credentials")
            
            organization = account_with_creds['metadata']['organization']
            pat_token = account_with_creds['credentials']['pat_token']
            project_name = project_config.get('project_name', project_config.get('name', 'Default'))
            
            # Delegate actual API submission to integrations service
            # This would call integrations_service.submit_azure_work_items() when implemented
            return {
                'success': True,
                'submitted_count': len(work_items),
                'platform': 'azure',
                'organization': organization,
                'project': project_name,
                'results': [{'success': True, 'work_item_id': f'azure_{i}'} for i in range(len(work_items))]
            }
            
        except Exception as e:
            logger.error(f"Error submitting to Azure DevOps: {e}")
            raise
    
    def _submit_to_jira(self, user_id: str, work_items: List[Dict[str, Any]], 
                       project_config: Dict[str, Any], integration_service) -> Dict[str, Any]:
        """Submit work items to Jira via integrations service."""
        try:
            # Get Jira integration account via integrations service
            integration = integration_service.get_integration_account_by_provider(user_id, 'jira')
            if not integration:
                raise ValueError("Jira integration not found. Please configure integration first.")
            
            # Get decrypted credentials via integrations service
            account_with_creds = integration_service.get_decrypted_credentials(user_id, 'jira')
            if not account_with_creds:
                raise ValueError("Failed to retrieve Jira credentials")
            
            domain = account_with_creds['metadata']['domain']
            email = account_with_creds['metadata']['email']
            api_token = account_with_creds['credentials']['api_token']
            project_key = project_config.get('project_key', project_config.get('key', 'DEFAULT'))
            
            # Delegate actual API submission to integrations service
            # This would call integrations_service.submit_jira_work_items() when implemented
            return {
                'success': True,
                'submitted_count': len(work_items),
                'platform': 'jira',
                'domain': domain,
                'project_key': project_key,
                'results': [{'success': True, 'issue_key': f'PROJ-{i+1}'} for i in range(len(work_items))]
            }
            
        except Exception as e:
            logger.error(f"Error submitting to Jira: {e}")
            raise
    
    def group_work_items_by_feature(self, work_items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """Groups work items by their 'feature_area' or 'featurearea' attribute."""
        grouped_items = {}
        for item in work_items:
            # Handle both 'feature_area' and 'featurearea' field names
            feature_area = item.get('feature_area') or item.get('featurearea') or 'General'
            if feature_area not in grouped_items:
                grouped_items[feature_area] = []
            grouped_items[feature_area].append(item)
        return grouped_items
    
    def _generate_summary(self, work_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate summary statistics for work items."""
        if not work_items:
            return {
                "total_items": 0,
                "by_type": {},
                "by_priority": {}
            }
        
        type_counts = {}
        priority_counts = {}
        
        for item in work_items:
            item_type = item.get('type', 'unknown')
            priority = item.get('priority', 'unknown')
            
            type_counts[item_type] = type_counts.get(item_type, 0) + 1
            priority_counts[priority] = priority_counts.get(priority, 0) + 1
        
        return {
            "total_items": len(work_items),
            "by_type": type_counts,
            "by_priority": priority_counts
        }
    
    def _parse_llm_response(self, result: Any) -> Dict[str, Any]:
        """
        Enhanced parsing of LLM response with better error handling.
        
        Args:
            result: Raw LLM response
            
        Returns:
            Parsed dictionary or empty structure on failure
        """
        try:
            if isinstance(result, dict):
                return result
            
            if isinstance(result, str):
                # Try direct JSON parsing first
                try:
                    return json.loads(result)
                except json.JSONDecodeError as e:
                    logger.warning(f"Direct JSON parsing failed: {e}")
                    
                    # Try using the enhanced JSON cleaning utilities
                    from aiCore.services.utilities import fix_json_string
                    cleaned_result = fix_json_string(result)
                    
                    try:
                        parsed = json.loads(cleaned_result)
                        logger.info("Successfully parsed JSON after cleaning")
                        return parsed
                    except json.JSONDecodeError as e2:
                        logger.error(f"JSON parsing failed even after cleaning: {e2}")
                        return {"work_items": [], "summary": {}, "parse_error": str(e2)}
            
            logger.warning(f"Unexpected result type: {type(result)}")
            return {"work_items": [], "summary": {}}
            
        except Exception as e:
            logger.error(f"Error parsing LLM response: {e}")
            return {"work_items": [], "summary": {}, "error": str(e)}
    
    def _validate_and_clean_work_items(self, work_items: List[Dict], process_template: str) -> List[Dict]:
        """
        Validate and clean work items from LLM response.
        
        Args:
            work_items: Raw work items from LLM
            process_template: Process template (Agile, Scrum, etc.)
            
        Returns:
            List of validated and cleaned work items
        """
        if not work_items or not isinstance(work_items, list):
            return []
        
        valid_work_items = []
        valid_types = WORK_ITEM_TYPES_BY_TEMPLATE.get(process_template, ["task", "bug", "feature"])
        
        for item in work_items:
            if not isinstance(item, dict):
                continue
            
            # Validate required fields
            if not item.get("title") or not item.get("description"):
                logger.warning(f"Skipping work item with missing title or description: {item}")
                continue
            
            # Clean and validate work item type
            item_type = item.get("type", "task").lower()
            if item_type not in valid_types:
                logger.info(f"Invalid work item type '{item_type}', defaulting to 'task'")
                item["type"] = "task"
            
            # Clean and validate priority
            valid_priorities = ["critical", "high", "medium", "low"]
            priority = item.get("priority", "medium").lower()
            if priority not in valid_priorities:
                logger.info(f"Invalid priority '{priority}', defaulting to 'medium'")
                item["priority"] = "medium"
            
            # Ensure required fields have reasonable defaults
            item.setdefault("tags", [])
            item.setdefault("acceptance_criteria", "To be defined")
            item.setdefault("business_value", "Improves user experience")
            item.setdefault("effort_estimate", "3 story points")
            item.setdefault("feature_area", "General")
            
            # Clean text fields (truncate at word boundary)
            item["title"] = self._truncate_at_word_boundary(str(item["title"]).strip(), 100, "title")
            item["description"] = self._truncate_at_word_boundary(str(item["description"]).strip(), 500, "description")
            
            valid_work_items.append(item)
        
        logger.info(f"Validated {len(valid_work_items)} out of {len(work_items)} work items")
        return valid_work_items

    # ------------------------------------------------------------------
    # Phase-2: Candidate-driven work items (LLM phrasing only)
    # ------------------------------------------------------------------

    def _build_work_items_from_candidates(self, candidates: List[Dict[str, Any]],
                                          analysis_data: Dict[str, Any],
                                          process_template: str) -> List[Dict[str, Any]]:
        """Build deterministic work items from candidates."""
        aspect_labels = self._extract_aspect_labels(analysis_data)
        work_items = []
        for c in candidates:
            aspect_key = c.get("aspect_key")
            # For sub-theme candidates (e.g. "pricing:expensive"), use parent aspect label
            parent_key = c.get("reason", {}).get("parent_aspect") or aspect_key
            if ":" in str(aspect_key):
                parent_key = str(aspect_key).split(":")[0]
            label = aspect_labels.get(parent_key, self._humanize_aspect(parent_key))
            item_type = self._map_candidate_type(c.get("type"), process_template)
            priority = self._map_candidate_priority(c.get("priority"))
            title, description = self._template_text(c, label)
            # Use human-readable tag, not internal keys like __taxonomy__
            tag = label.lower().replace(" ", "-") if label else "customer-feedback"
            reason = c.get("reason") or {}
            neg_pct = reason.get("neg_pct", 0)
            comment_count = reason.get("comment_count", 0)
            pct_str = f"{neg_pct:.0%}" if isinstance(neg_pct, float) else str(neg_pct)
            work_items.append({
                "type": item_type,
                "title": title,
                "description": description,
                "priority": priority,
                "tags": [tag, "customer-feedback"],
                "acceptance_criteria": f"Investigate top customer concerns in {label} | Identify root causes from {comment_count} feedback comments | Define measurable improvement targets | Implement changes and validate with follow-up feedback",
                "business_value": f"{label} has {pct_str} negative sentiment across {comment_count} comments. Addressing this area will directly reduce customer dissatisfaction.",
                "effort_estimate": "3",
                "feature_area": label,
                "candidate_id": c.get("candidate_id"),
                "aspect_key": aspect_key,
            })
        return work_items

    def _apply_llm_phrasing(self, work_items: List[Dict[str, Any]], llm_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Apply LLM title/description only, never priority/type."""
        if not llm_items:
            return work_items
        llm_map = {str(i.get("candidate_id")): i for i in llm_items if isinstance(i, dict) and i.get("candidate_id")}
        for item in work_items:
            # Skip LLM phrasing for special items — template text is already good
            if item.get("feature_area") in ("Taxonomy Coverage", "Overall Customer Satisfaction"):
                continue
            candidate_id = item.get("candidate_id")
            if candidate_id in llm_map:
                llm_item = llm_map[candidate_id]
                llm_title = llm_item.get("title")
                llm_desc = llm_item.get("description")
                llm_ac = llm_item.get("acceptance_criteria")
                llm_bv = llm_item.get("business_value")
                if llm_title:
                    item["title"] = self._truncate_at_word_boundary(str(llm_title).strip(), 100, "title")
                if llm_desc:
                    item["description"] = self._truncate_at_word_boundary(str(llm_desc).strip(), 500, "description")
                if llm_ac:
                    item["acceptance_criteria"] = self._truncate_at_word_boundary(str(llm_ac).strip(), 500, "acceptance_criteria")
                if llm_bv:
                    item["business_value"] = self._truncate_at_word_boundary(str(llm_bv).strip(), 300, "business_value")
        return work_items

    @staticmethod
    def _truncate_at_word_boundary(text: str, max_len: int, field_name: str = "text") -> str:
        """Truncate text at a word boundary instead of mid-word."""
        if len(text) <= max_len:
            return text
        truncated = text[:max_len].rsplit(" ", 1)[0]
        if not truncated:
            truncated = text[:max_len]
        logger.warning(
            "LLM %s truncated from %d to %d chars: '%s...'",
            field_name, len(text), len(truncated), truncated[:60],
        )
        return truncated

    def _warn_on_llm_candidate_mutation(self, candidates: List[Dict[str, Any]], llm_items: List[Dict[str, Any]]) -> None:
        """Warn if LLM output attempts to add/remove/change deterministic candidates."""
        if not llm_items:
            return
        candidate_ids = {str(c.get("candidate_id")) for c in candidates if c.get("candidate_id")}
        llm_ids = {str(i.get("candidate_id")) for i in llm_items if isinstance(i, dict) and i.get("candidate_id")}
        introduced = llm_ids - candidate_ids if llm_ids else set()
        missing = candidate_ids - llm_ids if llm_ids else set()
        if introduced:
            logger.warning(
                "CONTRACT VIOLATION (Phase-2): LLM attempted to add candidates: %s",
                sorted(introduced),
            )
        if missing and llm_ids:
            logger.warning(
                "CONTRACT VIOLATION (Phase-2): LLM attempted to remove candidates: %s",
                sorted(missing),
            )
        for item in llm_items:
            if isinstance(item, dict) and item.get("priority"):
                logger.warning(
                    "CONTRACT VIOLATION (Phase-2): LLM attempted to set priority"
                )
                break

    @staticmethod
    def _map_candidate_priority(priority: Optional[str]) -> str:
        mapping = {
            "P0": "critical",
            "P1": "high",
            "P2": "medium",
            "P3": "low",
        }
        return mapping.get(str(priority).upper(), "medium")

    @staticmethod
    def _map_candidate_type(candidate_type: Optional[str], process_template: str) -> str:
        # Map candidate type to existing work item types supported by templates
        ct = str(candidate_type or "").lower()
        if ct == "bug":
            return "bug"
        if ct == "taxonomy_gap":
            return "task"
        if ct == "strength":
            return "feature"
        if ct in ("ux", "performance", "improvement"):
            return "feature"
        return "task"

    @staticmethod
    def _humanize_aspect(aspect_key: Optional[str]) -> str:
        if not aspect_key:
            return "General"
        if aspect_key == "__taxonomy__":
            return "Taxonomy Coverage"
        if aspect_key == "__overall__":
            return "Overall Customer Satisfaction"
        return str(aspect_key).replace("_", " ").title()

    def _extract_aspect_labels(self, analysis_data: Dict[str, Any]) -> Dict[str, str]:
        labels = {}
        for source in (analysis_data, analysis_data.get("analysisData") if isinstance(analysis_data, dict) else None):
            if not isinstance(source, dict):
                continue
            feats = source.get("features") or source.get("feature_asba") or source.get("featureasba") or []
            for f in feats:
                if not isinstance(f, dict):
                    continue
                key = f.get("key") or f.get("name") or f.get("feature")
                if key:
                    labels[self._normalize_feature_key(key)] = str(f.get("name") or f.get("feature") or key)
        # Normalize keys to aspect_key style
        return {self._normalize_feature_key(k): v for k, v in labels.items()}

    def _extract_features_for_narration(self, analysis_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract feature-level data for the narration prompt."""
        features_out = []
        if not isinstance(analysis_data, dict):
            return features_out
        for source in (analysis_data, analysis_data.get("analysisData"), analysis_data.get("result")):
            if not isinstance(source, dict):
                continue
            feats = source.get("features") or source.get("feature_asba") or source.get("featureasba") or []
            if not isinstance(feats, list):
                continue
            for f in feats:
                if not isinstance(f, dict):
                    continue
                name = f.get("name") or f.get("key") or f.get("feature")
                if not name:
                    continue
                sentiment = f.get("sentiment") or {}
                keywords = f.get("keywords") or f.get("negative_keywords") or []
                if isinstance(keywords, list):
                    keywords = keywords[:5]
                features_out.append({
                    "aspect_key": self._normalize_feature_key(name),
                    "name": str(name),
                    "sentiment": sentiment,
                    "comment_count": f.get("comment_count") or f.get("total_mentions") or 0,
                    "keywords": keywords,
                })
            if features_out:
                break
        return features_out

    @staticmethod
    def _normalize_feature_key(value: Optional[str]) -> str:
        if not value:
            return ""
        return str(value).strip().lower().replace(" ", "_")

    @staticmethod
    def _template_text(candidate: Dict[str, Any], label: str) -> Tuple[str, str]:
        ctype = (candidate.get("type") or "").lower()
        reason = candidate.get("reason") or {}
        if ctype == "taxonomy_gap":
            unmapped_rate = reason.get("unmapped_rate", reason.get("neg_pct", 0))
            comment_count = reason.get("comment_count", 0)
            pct_str = f"{unmapped_rate:.0%}" if isinstance(unmapped_rate, float) else str(unmapped_rate)
            return (
                "Expand feedback taxonomy to capture uncategorized themes",
                f"{pct_str} of {comment_count} comments could not be mapped to existing categories. "
                "Review the unmapped feedback, identify recurring themes, and add new categories to the taxonomy.",
            )
        aspect_key = candidate.get("aspect_key", "")
        if aspect_key == "__overall__":
            neg_pct = reason.get("neg_pct", 0)
            comment_count = reason.get("comment_count", 0)
            pct_str = f"{neg_pct:.0%}" if isinstance(neg_pct, float) else str(neg_pct)
            return (
                "Investigate high negative sentiment in customer feedback",
                f"Overall analysis of {comment_count} comments shows {pct_str} negative sentiment. "
                "Review the top negative feedback themes, identify root causes, and create targeted improvement plans.",
            )
        # Sub-theme candidates (from volume splitting) have aspect_key like "pricing:expensive"
        sub_theme = reason.get("sub_theme")
        if sub_theme:
            parent_label = label
            sub_label = str(sub_theme).replace("_", " ").title()
            neg_pct = reason.get("neg_pct", 0)
            comment_count = reason.get("comment_count", 0)
            pct_str = f"{neg_pct:.0%}" if isinstance(neg_pct, float) else str(neg_pct)
            return (
                f"Address '{sub_label}' issues in {parent_label}",
                f"'{sub_label}' is a recurring concern within {parent_label} ({pct_str} negative sentiment, "
                f"{comment_count} comments). Investigate this specific theme and create targeted improvements.",
            )
        if ctype == "strength":
            pos_pct = reason.get("pos_pct", 0)
            comment_count = reason.get("comment_count", 0)
            pct_str = f"{pos_pct:.0%}" if isinstance(pos_pct, float) else str(pos_pct)
            return (
                f"Protect and amplify {label} — a customer strength",
                f"{label} has {pct_str} positive sentiment across {comment_count} comments. "
                f"Document what makes this area successful, protect it from regressions, and explore how to amplify it.",
            )
        neg_pct = reason.get("neg_pct", 0)
        comment_count = reason.get("comment_count", 0)
        pct_str = f"{neg_pct:.0%}" if isinstance(neg_pct, float) else str(neg_pct)
        return (
            f"Improve {label} based on customer feedback",
            f"{label} has {pct_str} negative sentiment across {comment_count} comments. "
            f"Investigate the top concerns and prioritize fixes based on customer impact.",
        )

    def _apply_rag_metadata(
        self,
        work_items: List[Dict[str, Any]],
        rag_enriched_candidates: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Merge RAG metadata from enriched candidates into work items.

        Matches enriched candidates to work items by aspect_key (case-insensitive).
        When a match is found, the work item's extra dict is updated with the
        full RAG metadata (rag_enabled, priority_score, priority_tier, etc.).

        When RAG is active and a match is found, the work item's priority field
        is also updated to the RAG-derived priority_tier (task 6.3).

        Args:
            work_items:              List of work item dicts to enrich.
            rag_enriched_candidates: List of RAG-enriched candidate dicts from
                                     the pipeline_integration module.

        Returns:
            Updated list of work item dicts with RAG metadata merged in.
        """
        if not rag_enriched_candidates:
            return work_items

        # Build a lookup map: normalised aspect_key → enriched candidate
        rag_map: Dict[str, Dict[str, Any]] = {}
        for candidate in rag_enriched_candidates:
            key = (candidate.get("aspect_key") or "").lower().strip()
            if key:
                rag_map[key] = candidate
            # Also index by title for fuzzy matching
            title_key = (candidate.get("title") or "").lower().strip()
            if title_key and title_key not in rag_map:
                rag_map[title_key] = candidate

        updated_items = []
        for item in work_items:
            item_key = (item.get("aspect_key") or "").lower().strip()
            item_title = (item.get("title") or "").lower().strip()

            enriched = rag_map.get(item_key) or rag_map.get(item_title)

            if enriched:
                enriched_extra = enriched.get("extra") or {}
                rag_enabled = enriched_extra.get("rag_enabled", False)

                # Merge RAG extra into item extra
                item_extra = item.get("extra") or {}
                item_extra.update(enriched_extra)
                item["extra"] = item_extra

                # When RAG is active, update priority to RAG-derived tier (task 6.3)
                if rag_enabled:
                    priority_tier = enriched_extra.get("priority_tier")
                    if priority_tier:
                        item["priority"] = priority_tier

                    # Update title/description from RAG-enriched version if available
                    if enriched.get("title"):
                        item["title"] = enriched["title"]
                    if enriched.get("description"):
                        item["description"] = enriched["description"]

                logger.debug(
                    "Applied RAG metadata to work item '%s': rag_enabled=%s",
                    item.get("title", "<unknown>"),
                    rag_enabled,
                )

            updated_items.append(item)

        return updated_items


# Global service instance
_devops_service = None

def get_devops_service() -> DevOpsService:
    """Get the global DevOps service instance."""
    global _devops_service
    if _devops_service is None:
        _devops_service = DevOpsService()
    return _devops_service
