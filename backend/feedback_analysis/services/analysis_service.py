"""
Analysis service for analysis-related business logic.
"""

from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import uuid
from ..repositories import AnalysisRepository
from aiCore.services.openai_client import get_azure_deployment_name
from .chunking_service import get_chunking_service
from authentication.services import get_authentication_service
from apis.core.error_handlers import handle_service_errors
from apis.infrastructure.cache_service import cache_analysis_result, get_cache_service
import logging

logger = logging.getLogger(__name__)


class AnalysisService:
    """Service for analysis business logic."""
    
    def __init__(self, project_service=None):
        self.analysis_repo = AnalysisRepository()
        self.chunking_service = get_chunking_service()
        self.auth_service = get_authentication_service()
        # Use dependency injection instead of local import
        self._project_service = project_service
    
    @property
    def project_service(self):
        """Lazy load project service to avoid circular imports."""
        if self._project_service is None:
            from integrations.services import get_project_service
            self._project_service = get_project_service()
        return self._project_service
    
    @handle_service_errors
    def create_analysis(self, user_id: str, analysis_data: Dict[str, Any], 
                       project_id: str = None) -> Dict[str, Any]:
        """Create a new analysis."""
        try:
            # Prepare analysis document
            analysis_doc = {
                "id": str(uuid.uuid4()),
                "userId": user_id,
                "analysisType": "project" if project_id else "personal",
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "updatedAt": datetime.now(timezone.utc).isoformat(),
                **analysis_data
            }
            
            if project_id:
                analysis_doc["projectId"] = project_id
            
            return self.analysis_repo.create(analysis_doc)
            
        except Exception as e:
            logger.error(f"Error creating analysis: {e}")
            raise
    
    def get_latest_project_analysis(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Get the latest analysis for a project. No caching — must always return fresh data."""
        return self.analysis_repo.get_latest_by_project(project_id)
    
    def get_latest_analysis_for_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Get the latest analysis for a project (alias for compatibility)."""
        return self.get_latest_project_analysis(project_id)
    
    @cache_analysis_result(ttl=600)  # Cache for 10 minutes
    def get_latest_personal_analysis(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get the latest personal analysis for a user."""
        return self.analysis_repo.get_latest_personal_by_user(user_id)
    
    @cache_analysis_result(ttl=1200)  # Cache for 20 minutes
    def get_project_analysis_history(self, project_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get analysis history for a project."""
        return self.analysis_repo.get_by_project(project_id, limit)
    
    def get_user_analysis_summary(self, user_id: str) -> Dict[str, Any]:
        """Get comprehensive analysis summary for a user."""
        try:
            # Get cumulative data
            cumulative_data = self.analysis_repo.get_cumulative_data_by_user(user_id)
            
            # Get latest personal analysis
            latest_personal = self.analysis_repo.get_latest_personal_by_user(user_id)
            
            result = {
                'cumulative': cumulative_data,
                'latest_personal': latest_personal
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Error getting user analysis summary: {e}")
            raise
    
    def create_sentiment_analysis(self, user_id: str, feedback_data: str, 
                                 analysis_results: Dict[str, Any], 
                                 project_id: str = None) -> Dict[str, Any]:
        """Create a sentiment analysis record."""
        try:
            # Chunk feedback data for optimal processing
            chunks = self.chunking_service.chunk_feedback_for_sentiment(feedback_data)
            
            analysis_data = {
                "analysisSubType": "sentiment",
                "feedbackData": feedback_data,
                "results": analysis_results,
                "chunks_info": {
                    "total_chunks": len(chunks),
                    "chunking_strategy": "sentiment_optimized"
                },
                "metadata": {
                    "analysisEngine": "azure_openai",
                    "modelUsed": get_azure_deployment_name(),
                    "processingTime": analysis_results.get('processing_time'),
                    "commentsProcessed": len(feedback_data.split('\n')) if feedback_data else 0,
                    "chunksProcessed": len(chunks)
                }
            }
            
            return self.create_analysis(user_id, analysis_data, project_id)
            
        except Exception as e:
            logger.error(f"Error creating sentiment analysis: {e}")
            raise
    
    def create_deep_analysis(self, user_id: str, feedback_data: str, 
                           work_items: List[Dict[str, Any]], platform: str,
                           project_id: str = None) -> Dict[str, Any]:
        """Create a deep analysis record with work items."""
        try:
            # Chunk feedback data for optimal deep analysis processing
            chunks = self.chunking_service.chunk_feedback_for_deep_analysis(feedback_data)
            
            analysis_data = {
                "analysisSubType": "deep_analysis",
                "feedbackData": feedback_data,
                "workItems": work_items,
                "platform": platform,
                "summary": self._generate_work_items_summary(work_items),
                "chunks_info": {
                    "total_chunks": len(chunks),
                    "chunking_strategy": "deep_analysis_optimized"
                },
                "metadata": {
                    "analysisEngine": "azure_openai",
                    "modelUsed": get_azure_deployment_name(),
                    "platform": platform,
                    "workItemsGenerated": len(work_items),
                    "commentsProcessed": len(feedback_data.split('\n')) if feedback_data else 0,
                    "chunksProcessed": len(chunks)
                }
            }
            
            return self.create_analysis(user_id, analysis_data, project_id)
            
        except Exception as e:
            logger.error(f"Error creating deep analysis: {e}")
            raise
    
    def _generate_work_items_summary(self, work_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate summary statistics for work items."""
        if not work_items:
            return {
                "total_items": 0,
                "by_type": {},
                "by_priority": {}
            }
        
        # Count by type
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

    def get_chunking_info(self, feedback_data: str) -> Dict[str, Any]:
        """Get information about how feedback would be chunked for analysis."""
        try:
            return self.chunking_service.get_chunk_info(feedback_data)
        except Exception as e:
            logger.error(f"Error getting chunking info: {e}")
            return {
                "error": str(e),
                "total_tokens": 0,
                "total_characters": len(feedback_data) if feedback_data else 0
            }
    
    # User and Project Context Methods
    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by primary key."""
        try:
            return self.auth_service.get_user_by_id(user_id)
        except Exception as e:
            logger.error(f"Error getting user by id: {e}")
            return None
    
    def ensure_project_context(self, project_id: str, user_id: str) -> tuple:
        """Ensure project context exists."""
        try:
            return self.project_service.ensure_project_context(project_id, user_id)
        except Exception as e:
            logger.error(f"Error ensuring project context: {e}")
            raise
    
    # Insights Methods
    def get_all_insights(self) -> List[Dict[str, Any]]:
        """Get all insights."""
        try:
            return self.analysis_repo.get_all_insights()
        except Exception as e:
            logger.error(f"Error getting all insights: {e}")
            raise
    
    def get_insight_by_id(self, insight_id: str) -> Optional[Dict[str, Any]]:
        """Get insight by ID."""
        try:
            return self.analysis_repo.get_insight_by_id(insight_id)
        except Exception as e:
            logger.error(f"Error getting insight by ID: {e}")
            return None
    
    def get_insights_by_type(self, analysis_type: str) -> List[Dict[str, Any]]:
        """Get insights by analysis type."""
        try:
            return self.analysis_repo.get_insights_by_type(analysis_type)
        except Exception as e:
            logger.error(f"Error getting insights by type: {e}")
            raise

    def get_insight_rules_for_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Get insight auto-rule configuration for a project."""
        try:
            return self.analysis_repo.get_insight_rules_for_project(project_id)
        except Exception as e:
            logger.error(f"Error getting insight rules for project: {e}")
            return None

    def upsert_insight_rules_for_project(self, project_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Upsert insight auto-rule configuration for a project."""
        try:
            return self.analysis_repo.upsert_insight_rules_for_project(project_id, data)
        except Exception as e:
            logger.error(f"Error upserting insight rules for project: {e}")
            return None

    def get_insight_reviews_for_project(self, project_id: str) -> List[Dict[str, Any]]:
        """Get insight review statuses for a project."""
        try:
            return self.analysis_repo.get_insight_reviews_for_project(project_id)
        except Exception as e:
            logger.error(f"Error getting insight reviews for project: {e}")
            return []

    def upsert_insight_review(self, project_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Upsert insight review status."""
        try:
            return self.analysis_repo.upsert_insight_review(project_id, data)
        except Exception as e:
            logger.error(f"Error upserting insight review: {e}")
            return None
    
    # Analysis Data Methods
    def save_analysis_data(self, analysis_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Save analysis data."""
        try:
            logger.info(f"🔍 DEBUG: AnalysisService.save_analysis_data called")
            logger.info(f"🔍 DEBUG: Input data keys: {list(analysis_data.keys())}")
            logger.info(f"🔍 DEBUG: Input data id: {analysis_data.get('id')}")
            logger.info(f"🔍 DEBUG: Input data projectId: {analysis_data.get('projectId')}")
            logger.info(f"🔍 DEBUG: Input data userId: {analysis_data.get('userId')}")
            logger.info(f"🔍 DEBUG: Input data has original_comments: {'original_comments' in analysis_data}")
            logger.info(f"🔍 DEBUG: Input data has feedback: {'feedback' in analysis_data}")
            
            if 'original_comments' in analysis_data:
                logger.info(f"🔍 DEBUG: original_comments count: {len(analysis_data['original_comments'])}")
            if 'feedback' in analysis_data:
                logger.info(f"🔍 DEBUG: feedback count: {len(analysis_data['feedback'])}")
            
            result = self.analysis_repo.save_analysis_data(analysis_data)

            if result:
                logger.info(f"✅ AnalysisService.save_analysis_data SUCCESS")
                logger.info(f"🔍 DEBUG: Returned result keys: {list(result.keys())}")
                logger.info(f"🔍 DEBUG: Returned result id: {result.get('id')}")

                # Clear any remaining analysis cache entries
                try:
                    cache = get_cache_service()
                    cache.clear_pattern("analysis:*")
                    logger.info("🗑️ Cleared analysis cache entries")
                except Exception as cache_err:
                    logger.warning(f"Failed to clear cache: {cache_err}")
            else:
                logger.error(f"❌ AnalysisService.save_analysis_data FAILED - returned None")
            
            return result
        except Exception as e:
            logger.error(f"Error saving analysis data: {e}", exc_info=True)
            return None
    
    def analysis_has_result(self, analysis_id: str) -> bool:
        """Idempotency check: has this analysis already been processed and saved?"""
        try:
            return self.analysis_repo.analysis_has_result(analysis_id)
        except Exception as e:
            logger.warning(f"analysis_has_result check failed (treating as not complete): {e}")
            return False

    def mark_analysis_status(self, analysis_id: str, status: str, **kwargs) -> None:
        """Durably record an analysis lifecycle status in Neon (see repository)."""
        self.analysis_repo.mark_analysis_status(analysis_id, status, **kwargs)

    def get_status_by_task_id(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Durable lifecycle status for a Celery task id, or None."""
        try:
            return self.analysis_repo.get_status_by_task_id(task_id)
        except Exception as e:
            logger.warning(f"get_status_by_task_id failed for {task_id}: {e}")
            return None

    def update_project_last_analysis(self, project_id: str, analysis_id: str) -> bool:
        """Update project's last analysis."""
        try:
            return self.analysis_repo.update_project_last_analysis(project_id, analysis_id)
        except Exception as e:
            logger.error(f"Error updating project last analysis: {e}")
            return False
    
    # User Data Methods
    def get_latest_personal_user_data(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get latest personal user data."""
        try:
            return self.analysis_repo.get_latest_personal_user_data(user_id)
        except Exception as e:
            logger.error(f"Error getting latest personal user data: {e}")
            return None
    
    def get_user_data_by_project(self, user_id: str, project_id: str) -> Optional[Dict[str, Any]]:
        """Get user data by project."""
        try:
            return self.analysis_repo.get_user_data_by_project(user_id, project_id)
        except Exception as e:
            logger.error(f"Error getting user data by project: {e}")
            return None
    
    def get_latest_analysis_by_project(self, project_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Get latest analysis data for a project."""
        try:
            return self.analysis_repo.get_latest_analysis_by_project(project_id, user_id)
        except Exception as e:
            logger.error(f"Error getting latest analysis by project: {e}")
            return None
    
    def get_analysis_by_id(self, analysis_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Get specific analysis by ID."""
        try:
            return self.analysis_repo.get_analysis_by_id(analysis_id, user_id)
        except Exception as e:
            logger.error(f"Error getting analysis by ID: {e}")
            return None

    def get_analysis_by_id_any(self, analysis_id: str) -> Optional[Dict[str, Any]]:
        """Get analysis by ID without user ownership filter."""
        try:
            return self.analysis_repo.get_analysis_by_id_any(analysis_id)
        except Exception as e:
            logger.error(f"Error getting analysis by ID (any user): {e}")
            return None

    def delete_analysis(self, analysis_id: str, user_id: str) -> bool:
        """Delete an analysis by ID (owner-only)."""
        try:
            return self.analysis_repo.delete_analysis(analysis_id, user_id)
        except Exception as e:
            logger.error(f"Error deleting analysis: {e}")
            return False

    def update_analysis_name(self, analysis_id: str, user_id: str, name: Optional[str]) -> Optional[Dict[str, Any]]:
        """Update a stored analysis run name (owner-only)."""
        try:
            analysis = self.analysis_repo.get_analysis_by_id(analysis_id, user_id)
            if not analysis:
                return None

            project_id = analysis.get("projectId") or analysis.get("project_id")
            if not project_id:
                logger.error("Analysis update failed: missing projectId on analysis document.")
                return None

            now = datetime.now(timezone.utc).isoformat()
            if name:
                analysis["name"] = name
            else:
                analysis.pop("name", None)
            analysis["updatedAt"] = now
            analysis["name_updated_at"] = now
            analysis["name_updated_by"] = str(user_id)

            return self.analysis_repo.update_analysis(project_id, analysis)
        except Exception as e:
            logger.error(f"Error updating analysis name: {e}")
            return None

    def update_analysis_name_for_doc(self, analysis: Dict[str, Any], user_id: str, name: Optional[str]) -> Optional[Dict[str, Any]]:
        """Update a stored analysis run name for a fetched analysis document."""
        try:
            if not analysis or not analysis.get("id"):
                return None

            project_id = analysis.get("projectId") or analysis.get("project_id")
            if not project_id:
                logger.error("Analysis update failed: missing projectId on analysis document.")
                return None

            now = datetime.now(timezone.utc).isoformat()
            if name:
                analysis["name"] = name
            else:
                analysis.pop("name", None)
            analysis["updatedAt"] = now
            analysis["name_updated_at"] = now
            analysis["name_updated_by"] = str(user_id)

            return self.analysis_repo.update_analysis(project_id, analysis)
        except Exception as e:
            logger.error(f"Error updating analysis name: {e}")
            return None
    
    # Analysis History Methods
    def get_analysis_history_for_project(self, project_id: str) -> List[Dict[str, Any]]:
        """Get analysis history for project."""
        try:
            return self.analysis_repo.get_analysis_history_for_project(project_id)
        except Exception as e:
            logger.error(f"Error getting analysis history for project: {e}")
            return []
    
    def get_analysis_by_quarter(self, project_id: str, quarter: str) -> Optional[Dict[str, Any]]:
        """Get analysis by quarter."""
        try:
            return self.analysis_repo.get_analysis_by_quarter(project_id, quarter)
        except Exception as e:
            logger.error(f"Error getting analysis by quarter: {e}")
            return None
    
    def get_cumulative_analysis_for_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Get cumulative analysis for project."""
        try:
            return self.analysis_repo.get_cumulative_analysis_for_project(project_id)
        except Exception as e:
            logger.error(f"Error getting cumulative analysis for project: {e}")
            return None
    
    # Generic Query Methods
    def query_items(self, container_name: str, query: str, parameters: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Generic query method."""
        try:
            return self.analysis_repo.query_items(container_name, query, parameters)
        except Exception as e:
            logger.error(f"Error querying items: {e}")
            return []
    
    def patch_user_story(self, user_story_id: str, partition_key: str, patch_operations: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Patch user story."""
        try:
            return self.analysis_repo.patch_user_story(user_story_id, partition_key, patch_operations)
        except Exception as e:
            logger.error(f"Error patching user story: {e}")
            return None


# Global service instance
_analysis_service = None

def get_analysis_service(project_service=None) -> AnalysisService:
    """Get the global analysis service instance with optional dependency injection."""
    global _analysis_service
    if _analysis_service is None:
        _analysis_service = AnalysisService(project_service=project_service)
    return _analysis_service

def reset_analysis_service():
    """Reset the global service instance (useful for testing)."""
    global _analysis_service
    _analysis_service = None
