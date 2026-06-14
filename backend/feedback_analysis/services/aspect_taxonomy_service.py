"""
Aspect Taxonomy Service - Production-grade aspect management.

Versioned, frozen taxonomies. Aspects are treated as versioned artifacts that are:
- Generated once per domain/project
- Edited by users through UI
- Passed as input to processing pipeline
- Never regenerated during analysis runs

This eliminates non-determinism and latency variance from aspect generation.
"""

import logging
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class AspectTaxonomy:
    """
    Versioned aspect taxonomy for a domain/project.
    
    Attributes:
        taxonomy_id: Unique identifier for this taxonomy
        domain: Industry/service domain (e.g., "SaaS", "E-commerce", "Hospitality")
        aspects: List of aspect names
        version: Semantic version (e.g., "1.0.0", "1.1.0")
        created_at: ISO timestamp of creation
        updated_at: ISO timestamp of last update
        created_by: User who created this taxonomy
        description: Optional description of the taxonomy
        is_active: Whether this taxonomy is currently active
        source: Source of taxonomy (user, gpt, imported, system)
        last_unmapped_rate: Last run's unmapped rate for quality gating
        last_avg_aspects_per_comment: Last run's average aspects per comment
        is_pinned: Whether user has explicitly pinned this taxonomy
    """
    taxonomy_id: str
    domain: str
    aspects: List[str]
    version: str
    created_at: str
    updated_at: str
    created_by: str
    description: Optional[str] = None
    is_active: bool = True
    source: str = "user"
    last_unmapped_rate: Optional[float] = None
    last_avg_aspects_per_comment: Optional[float] = None
    is_pinned: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AspectTaxonomy':
        """Create from dictionary."""
        return cls(**data)


class AspectTaxonomyService:
    """
    Service for managing versioned aspect taxonomies.
    
    Provides CRUD operations for aspect taxonomies and validation
    for processing pipeline inputs.
    """
    
    # Default taxonomies for common domains
    DEFAULT_TAXONOMIES = {
        "SaaS": [
            "User Interface",
            "Performance",
            "Features",
            "Support",
            "Pricing",
            "Integration",
            "Security",
            "Documentation"
        ],
        "E-commerce": [
            "Product Quality",
            "Shipping",
            "Customer Service",
            "Website Experience",
            "Pricing",
            "Payment Process",
            "Returns",
            "Product Selection"
        ],
        "Hospitality": [
            "Room Quality",
            "Service",
            "Location",
            "Amenities",
            "Food & Beverage",
            "Cleanliness",
            "Value",
            "Check-in/Check-out"
        ],
        "Healthcare": [
            "Care Quality",
            "Staff",
            "Wait Times",
            "Facilities",
            "Communication",
            "Billing",
            "Accessibility",
            "Follow-up"
        ],
        "Education": [
            "Course Content",
            "Instruction",
            "Platform",
            "Support",
            "Assessment",
            "Resources",
            "Engagement",
            "Technical Issues"
        ]
    }
    
    def __init__(self):
        """Initialize the aspect taxonomy service."""
        self.taxonomies: Dict[str, AspectTaxonomy] = {}
        self._load_default_taxonomies()
    
    def _load_default_taxonomies(self) -> None:
        """Load default taxonomies for common domains."""
        current_time = datetime.utcnow().isoformat()
        
        for domain, aspects in self.DEFAULT_TAXONOMIES.items():
            taxonomy_id = f"default_{domain.lower().replace(' ', '_')}"
            
            taxonomy = AspectTaxonomy(
                taxonomy_id=taxonomy_id,
                domain=domain,
                aspects=aspects,
                version="1.0.0",
                created_at=current_time,
                updated_at=current_time,
                created_by="system",
                description=f"Default aspect taxonomy for {domain} domain",
                is_active=True,
                source="system"
            )
            
            self.taxonomies[taxonomy_id] = taxonomy
        
        logger.debug("Loaded default taxonomies", extra={"count": len(self.DEFAULT_TAXONOMIES)})
    
    def create_taxonomy(self, domain: str, aspects: List[str], 
                       created_by: str, description: Optional[str] = None) -> AspectTaxonomy:
        """
        Create a new aspect taxonomy.
        
        Args:
            domain: Industry/service domain
            aspects: List of aspect names
            created_by: User creating the taxonomy
            description: Optional description
            
        Returns:
            Created AspectTaxonomy
            
        Raises:
            ValueError: If aspects list is empty or contains duplicates
        """
        if not aspects:
            raise ValueError("Aspects list cannot be empty")
        
        if len(aspects) != len(set(aspects)):
            raise ValueError("Aspects list contains duplicates")
        
        # Validate aspect names
        for aspect in aspects:
            if not isinstance(aspect, str) or not aspect.strip():
                raise ValueError(f"Invalid aspect name: {aspect}")
        
        current_time = datetime.utcnow().isoformat()
        taxonomy_id = f"{domain.lower().replace(' ', '_')}_{int(datetime.utcnow().timestamp())}"
        
        taxonomy = AspectTaxonomy(
            taxonomy_id=taxonomy_id,
            domain=domain,
            aspects=[aspect.strip() for aspect in aspects],
            version="1.0.0",
            created_at=current_time,
            updated_at=current_time,
            created_by=created_by,
            description=description,
            is_active=True
        )
        
        self.taxonomies[taxonomy_id] = taxonomy
        logger.info(
            "Created taxonomy",
            extra={"taxonomy_id": taxonomy_id, "domain": domain, "aspect_count": len(aspects)},
        )

        return taxonomy
    
    def get_taxonomy(self, taxonomy_id: str) -> Optional[AspectTaxonomy]:
        """
        Get a taxonomy by ID.
        
        Args:
            taxonomy_id: Taxonomy identifier
            
        Returns:
            AspectTaxonomy if found, None otherwise
        """
        return self.taxonomies.get(taxonomy_id)
    
    def get_taxonomy_by_domain(self, domain: str) -> Optional[AspectTaxonomy]:
        """
        Get the active taxonomy for a domain.
        
        Args:
            domain: Domain name
            
        Returns:
            Active AspectTaxonomy for the domain, None if not found
        """
        for taxonomy in self.taxonomies.values():
            if taxonomy.domain.lower() == domain.lower() and taxonomy.is_active:
                return taxonomy
        return None
    
    def list_taxonomies(self, active_only: bool = True) -> List[AspectTaxonomy]:
        """
        List all taxonomies.
        
        Args:
            active_only: If True, return only active taxonomies
            
        Returns:
            List of AspectTaxonomy objects
        """
        taxonomies = list(self.taxonomies.values())
        
        if active_only:
            taxonomies = [t for t in taxonomies if t.is_active]
        
        # Sort by domain, then by version
        taxonomies.sort(key=lambda t: (t.domain, t.version))
        
        return taxonomies
    
    def update_taxonomy(self, taxonomy_id: str, aspects: List[str], 
                       updated_by: str, description: Optional[str] = None) -> AspectTaxonomy:
        """
        Update an existing taxonomy (creates new version).
        
        Args:
            taxonomy_id: Taxonomy to update
            aspects: New list of aspects
            updated_by: User making the update
            description: Optional new description
            
        Returns:
            Updated AspectTaxonomy
            
        Raises:
            ValueError: If taxonomy not found or aspects invalid
        """
        if taxonomy_id not in self.taxonomies:
            raise ValueError(f"Taxonomy '{taxonomy_id}' not found")
        
        if not aspects:
            raise ValueError("Aspects list cannot be empty")
        
        if len(aspects) != len(set(aspects)):
            raise ValueError("Aspects list contains duplicates")
        
        current_taxonomy = self.taxonomies[taxonomy_id]
        current_time = datetime.utcnow().isoformat()
        
        # Increment version (simple semantic versioning)
        version_parts = current_taxonomy.version.split('.')
        minor_version = int(version_parts[1]) + 1
        new_version = f"{version_parts[0]}.{minor_version}.0"
        
        # Update taxonomy
        current_taxonomy.aspects = [aspect.strip() for aspect in aspects]
        current_taxonomy.version = new_version
        current_taxonomy.updated_at = current_time
        if description is not None:
            current_taxonomy.description = description
        
        logger.info(
            "Updated taxonomy",
            extra={"taxonomy_id": taxonomy_id, "version": new_version, "aspect_count": len(aspects)},
        )

        return current_taxonomy
    
    def should_reuse_taxonomy(self, taxonomy: AspectTaxonomy, max_age_days: int = 30) -> Dict[str, Any]:
        """
        Check if taxonomy should be reused based on quality gates.
        
        Production rule: Don't perpetuate bad taxonomies forever.
        Reuse only if:
        - unmapped_rate_last_run ≤ 15%
        - avg_aspects_per_comment is sane (≤1.3)
        - taxonomy_age < max_age_days OR user explicitly pinned it
        
        Args:
            taxonomy: Taxonomy to check
            max_age_days: Maximum age in days before refresh prompt
            
        Returns:
            Dict with reuse decision and reasoning
        """
        from datetime import datetime, timedelta, timezone

        current_time = datetime.now(timezone.utc)
        created_time = datetime.fromisoformat(taxonomy.created_at.replace('Z', '+00:00'))
        if created_time.tzinfo is None:
            created_time = created_time.replace(tzinfo=timezone.utc)
        age_days = (current_time - created_time).days
        
        # Always reuse if user pinned it
        if taxonomy.is_pinned:
            return {
                "should_reuse": True,
                "reason": "User has pinned this taxonomy",
                "quality_gate_passed": True
            }
        
        # Check quality metrics if available
        quality_issues = []
        
        if taxonomy.last_unmapped_rate is not None:
            if taxonomy.last_unmapped_rate > 0.15:  # 15% threshold
                quality_issues.append(f"High unmapped rate: {taxonomy.last_unmapped_rate:.1%}")
        
        if taxonomy.last_avg_aspects_per_comment is not None:
            if taxonomy.last_avg_aspects_per_comment > 1.3:
                quality_issues.append(f"Too many aspects per comment: {taxonomy.last_avg_aspects_per_comment:.2f}")
        
        # Check age
        is_stale = age_days > max_age_days
        if is_stale:
            quality_issues.append(f"Taxonomy is {age_days} days old (max: {max_age_days})")
        
        # Decision logic
        if quality_issues:
            return {
                "should_reuse": False,
                "reason": f"Quality gate failed: {'; '.join(quality_issues)}",
                "quality_gate_passed": False,
                "suggested_action": "Refresh taxonomy or review aspect definitions",
                "age_days": age_days,
                "quality_issues": quality_issues
            }
        
        return {
            "should_reuse": True,
            "reason": "Quality gates passed",
            "quality_gate_passed": True,
            "age_days": age_days
        }
    
    def update_taxonomy_metrics(self, taxonomy_id: str, unmapped_rate: float, 
                               avg_aspects_per_comment: float) -> None:
        """
        Update taxonomy quality metrics after a processing run.
        
        Args:
            taxonomy_id: Taxonomy to update
            unmapped_rate: Unmapped rate from last run (0.0-1.0)
            avg_aspects_per_comment: Average aspects per comment from last run
        """
        if taxonomy_id not in self.taxonomies:
            logger.warning("Skipping metrics update: unknown taxonomy", extra={"taxonomy_id": taxonomy_id})
            return
        
        taxonomy = self.taxonomies[taxonomy_id]
        taxonomy.last_unmapped_rate = unmapped_rate
        taxonomy.last_avg_aspects_per_comment = avg_aspects_per_comment
        taxonomy.updated_at = datetime.utcnow().isoformat()
        
        logger.debug(
            "Updated taxonomy metrics",
            extra={
                "taxonomy_id": taxonomy_id,
                "unmapped_rate": unmapped_rate,
                "avg_aspects_per_comment": avg_aspects_per_comment,
            },
        )
    
    def pin_taxonomy(self, taxonomy_id: str, pinned: bool = True) -> AspectTaxonomy:
        """
        Pin or unpin a taxonomy to prevent automatic refresh prompts.
        
        Args:
            taxonomy_id: Taxonomy to pin/unpin
            pinned: Whether to pin (True) or unpin (False)
            
        Returns:
            Updated AspectTaxonomy
            
        Raises:
            ValueError: If taxonomy not found
        """
        if taxonomy_id not in self.taxonomies:
            raise ValueError(f"Taxonomy '{taxonomy_id}' not found")
        
        taxonomy = self.taxonomies[taxonomy_id]
        taxonomy.is_pinned = pinned
        taxonomy.updated_at = datetime.utcnow().isoformat()
        
        action = "pinned" if pinned else "unpinned"
        logger.info("Taxonomy pin state changed", extra={"taxonomy_id": taxonomy_id, "action": action})

        return taxonomy

    def validate_aspects_for_processing(self, aspects):
        """Validate aspects list for processing pipeline."""
        if not aspects:
            return {
                "valid": False,
                "error": "Aspects list cannot be empty",
                "suggestions": ["Use get_taxonomy_by_domain() to get default aspects"]
            }

        if len(aspects) != len(set(aspects)):
            duplicates = [aspect for aspect in aspects if aspects.count(aspect) > 1]
            return {
                "valid": False,
                "error": f"Duplicate aspects found: {duplicates}",
                "suggestions": ["Remove duplicate aspects from the list"]
            }

        invalid_aspects = [aspect for aspect in aspects if not isinstance(aspect, str) or not aspect.strip()]
        if invalid_aspects:
            return {
                "valid": False,
                "error": f"Invalid aspect names: {invalid_aspects}",
                "suggestions": ["Ensure all aspects are non-empty strings"]
            }

        if len(aspects) > 15:
            return {
                "valid": False,
                "error": f"Too many aspects ({len(aspects)}). Maximum recommended: 15",
                "suggestions": ["Consider grouping related aspects or using broader categories"]
            }

        if len(aspects) < 3:
            return {
                "valid": False,
                "error": f"Too few aspects ({len(aspects)}). Minimum recommended: 3",
                "suggestions": ["Add more aspects for better classification granularity"]
            }

        return {
            "valid": True,
            "aspect_count": len(aspects),
            "suggestions": []
        }


# Singleton instance
_aspect_taxonomy_service = None

def get_aspect_taxonomy_service() -> AspectTaxonomyService:
    """Get the singleton instance of AspectTaxonomyService."""
    global _aspect_taxonomy_service
    if _aspect_taxonomy_service is None:
        _aspect_taxonomy_service = AspectTaxonomyService()
    return _aspect_taxonomy_service