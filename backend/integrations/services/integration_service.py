"""
Integration service for managing external platform integrations.

This service handles the business logic for creating, managing, and testing
integrations with external platforms like Azure DevOps and Jira.
"""

from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import uuid
import logging

from ..repositories import IntegrationsRepository
from .external_api_service import get_external_api_service
from .organization_service import get_organization_service

logger = logging.getLogger(__name__)


class IntegrationService:
    """Service for integration business logic."""
    
    def __init__(self):
        self.integrations_repo = IntegrationsRepository()
        self.external_api_service = get_external_api_service()
        self.organization_service = get_organization_service()

    def _require_org_admin(self, organization_id: str, user_id: str) -> Dict[str, Any]:
        membership = self.organization_service.require_membership(str(organization_id), str(user_id))
        if not self.organization_service.has_min_role(membership, "admin"):
            raise ValueError("Only workspace admins can manage integrations.")
        return membership

    def _get_active_organization_id_for_user(self, user_id: str) -> Optional[str]:
        user = self.organization_service.user_repo.get_by_id(str(user_id))
        if not user:
            return None
        profile = user.get("profile") or {}
        if isinstance(profile, dict):
            return profile.get("active_organization_id")
        return None

    def _resolve_organization_id(self, user_id: str, organization_id: Optional[str] = None) -> str:
        resolved = str(organization_id or self._get_active_organization_id_for_user(user_id) or "").strip()
        if not resolved:
            raise ValueError("Active organization is required.")
        return resolved
    
    def _get_default_scopes(self, provider: str) -> List[str]:
        """Get default scopes for a provider."""
        scopes_map = {
            "azure": ["vso.project", "vso.code", "vso.work"],
            "jira": ["read:project", "write:issue", "read:issue"]
        }
        return scopes_map.get(provider, [])
    
    def _create_integration_account_document(
        self,
        user_id: str,
        organization_id: str,
        provider: str,
        credentials: Dict[str, Any],
        metadata: Dict[str, Any],
        display_name: str = None,
    ) -> Dict[str, Any]:
        """Create a new integration account document for PostgreSQL."""
        account_id = f"ia_{uuid.uuid4().hex[:12]}"
        
        return {
            "id": account_id,
            "type": "integrationAccount",
            "userId": user_id,  # This is the partition key for integrations container
            "organizationId": organization_id,
            "provider": provider,
            "displayName": display_name or f"{metadata.get('organization', metadata.get('domain', 'Unknown'))} ({provider.title()})",
            "status": "active",
            "credentials": credentials,
            "metadata": metadata,
            "scopes": self._get_default_scopes(provider),
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "expiresAt": None,
            "schemaVersion": 1
        }
    
    def _validate_integration_account(self, account_data: Dict[str, Any]) -> bool:
        """Validate integration account data."""
        required_fields = ["userId", "organizationId", "provider", "credentials", "metadata"]
        
        for field in required_fields:
            if field not in account_data:
                raise ValueError(f"Missing required field: {field}")
        
        if account_data["provider"] not in ["azure", "jira"]:
            raise ValueError("Provider must be 'azure' or 'jira'")
        
        return True
    
    def get_integration_accounts_by_user(self, user_id: str, organization_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all integration accounts for a user within an organization."""
        try:
            if organization_id:
                self.organization_service.require_membership(str(organization_id), str(user_id))
                return self.integrations_repo.get_by_organization(str(organization_id))
            return self.integrations_repo.get_integration_accounts_by_user(user_id)
        except Exception as e:
            logger.error(f"Error getting integration accounts for user {user_id}: {e}")
            raise

    def get_integration_account_for_display(
        self,
        user_id: str,
        account_id: str,
        organization_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if organization_id:
            self._require_org_admin(str(organization_id), str(user_id))
        return self.integrations_repo.get_integration_account_for_display(
            user_id,
            account_id,
            organization_id=organization_id,
        )

    def _get_saved_account_for_display(
        self,
        user_id: str,
        account_id: str,
        organization_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        account = self.get_integration_account_for_display(
            user_id,
            account_id,
            organization_id=organization_id,
        )
        if not account:
            raise ValueError("Integration account not found after save")
        return account

    def get_integration_account_by_provider(
        self,
        user_id: str,
        provider: str,
        organization_id: Optional[str] = None,
        *,
        include_credentials: bool = False,
    ) -> Optional[Dict[str, Any]]:
        resolved_org_id = self._resolve_organization_id(user_id, organization_id)
        self.organization_service.require_membership(resolved_org_id, str(user_id))
        account = self.integrations_repo.get_by_organization_and_provider(resolved_org_id, provider)
        if not account:
            return None
        if not include_credentials:
            account = self.integrations_repo.get_integration_account_for_display(
                user_id,
                account["id"],
                organization_id=resolved_org_id,
            )
        return account

    def get_decrypted_credentials(
        self,
        user_id: str,
        provider: str,
        organization_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        resolved_org_id = self._resolve_organization_id(user_id, organization_id)
        self.organization_service.require_membership(resolved_org_id, str(user_id))
        account = self.get_integration_account_by_provider(
            user_id,
            provider,
            organization_id=resolved_org_id,
            include_credentials=True,
        )
        if not account:
            return None

        from .encryption_service import get_encryption_service

        credentials = dict(account.get("credentials") or {})
        encrypted_token = credentials.get("tokenEncrypted") or credentials.get("token")
        if not encrypted_token:
            raise ValueError(f"{provider.title()} integration credentials are not configured.")

        decrypted_token = get_encryption_service().decrypt_token(encrypted_token)
        decrypted_credentials = dict(credentials)
        if provider == "azure":
            decrypted_credentials["pat_token"] = decrypted_token
        elif provider == "jira":
            decrypted_credentials["api_token"] = decrypted_token
        else:
            raise ValueError(f"Unsupported provider: {provider}")

        return {
            **account,
            "credentials": decrypted_credentials,
        }
    
    def create_azure_integration(self, user_id: str, organization_id: str, organization: str, pat_token: str) -> Dict[str, Any]:
        """
        Create or update Azure DevOps integration account.
        
        Args:
            user_id: User ID
            organization: Azure DevOps organization name
            pat_token: Personal Access Token
            
        Returns:
            Created/updated integration account
            
        Raises:
            ValueError: If connection test fails or validation errors
        """
        try:
            # Validate inputs
            if not organization_id or not organization or not pat_token:
                raise ValueError("Organization ID, organization, and PAT token are required")

            self._require_org_admin(str(organization_id), str(user_id))
            
            # Test the connection first
            test_result = self.external_api_service.test_azure_connection(organization, pat_token)
            if not test_result['success']:
                raise ValueError(f"Connection test failed: {test_result['error']}")
            
            # Encrypt the token
            from .encryption_service import get_encryption_service
            encryption_service = get_encryption_service()
            encrypted_pat = encryption_service.encrypt_token(pat_token)
            
            # Create account document
            credentials = {
                "tokenEncrypted": encrypted_pat,
                "tokenType": "pat"
            }
            
            metadata = {
                "organization": organization,
                "baseUrl": f"https://dev.azure.com/{organization}"
            }
            
            account_data = self._create_integration_account_document(
                user_id=user_id,
                organization_id=organization_id,
                provider="azure",
                credentials=credentials,
                metadata=metadata,
                display_name=f"{organization} (Azure DevOps)"
            )
            
            self._validate_integration_account(account_data)
            
            saved = self.integrations_repo.create_or_update_integration_account(account_data)
            return self._get_saved_account_for_display(
                user_id,
                saved["id"],
                organization_id=organization_id,
            )
            
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error creating Azure integration: {e}")
            raise
    
    def create_jira_integration(self, user_id: str, organization_id: str, domain: str, email: str, api_token: str) -> Dict[str, Any]:
        """
        Create or update Jira integration account.
        
        Args:
            user_id: User ID
            domain: Jira domain
            email: User email
            api_token: Jira API token
            
        Returns:
            Created/updated integration account
            
        Raises:
            ValueError: If connection test fails or validation errors
        """
        try:
            # Validate inputs
            if not organization_id or not domain or not email or not api_token:
                raise ValueError("Organization ID, domain, email, and API token are required")

            self._require_org_admin(str(organization_id), str(user_id))
            
            # Test the connection first
            test_result = self.external_api_service.test_jira_connection(domain, email, api_token)
            if not test_result['success']:
                raise ValueError(f"Connection test failed: {test_result['error']}")
            
            # Encrypt the token
            from .encryption_service import get_encryption_service
            encryption_service = get_encryption_service()
            encrypted_token = encryption_service.encrypt_token(api_token)
            
            # Handle domain format properly - normalize domain
            normalized_domain = domain.replace('.atlassian.net', '') if domain.endswith('.atlassian.net') else domain
            base_url = f"https://{normalized_domain}.atlassian.net"
            
            # Create account document
            credentials = {
                "tokenEncrypted": encrypted_token,
                "email": email,
                "tokenType": "api_token"
            }
            
            metadata = {
                "domain": domain,
                "email": email,
                "baseUrl": base_url
            }
            
            account_data = self._create_integration_account_document(
                user_id=user_id,
                organization_id=organization_id,
                provider="jira",
                credentials=credentials,
                metadata=metadata,
                display_name=f"{domain} (Jira)"
            )
            
            self._validate_integration_account(account_data)
            
            saved = self.integrations_repo.create_or_update_integration_account(account_data)
            return self._get_saved_account_for_display(
                user_id,
                saved["id"],
                organization_id=organization_id,
            )
            
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error creating Jira integration: {e}")
            raise
    
    def test_integration_connection(self, user_id: str, account_id: str, organization_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Test connection for an existing integration account.
        
        Args:
            user_id: User ID
            account_id: Integration account ID
            
        Returns:
            Test result with success status and details
        """
        try:
            # Get the integration account
            if organization_id:
                self._require_org_admin(str(organization_id), str(user_id))
            account = self.integrations_repo.get_integration_account(user_id, account_id, organization_id=organization_id)
            if not account:
                raise ValueError("Integration account not found")
            
            provider = account.get('provider')
            credentials = account.get('credentials', {})
            metadata = account.get('metadata', {})
            
            # Decrypt credentials
            from .encryption_service import get_encryption_service
            encryption_service = get_encryption_service()
            
            if provider == 'azure':
                organization = metadata.get('organization')
                encrypted_pat = credentials.get('tokenEncrypted')
                pat_token = encryption_service.decrypt_token(encrypted_pat)
                return self.external_api_service.test_azure_connection(organization, pat_token)
            elif provider == 'jira':
                domain = metadata.get('domain')
                email = metadata.get('email')
                encrypted_token = credentials.get('tokenEncrypted')
                api_token = encryption_service.decrypt_token(encrypted_token)
                return self.external_api_service.test_jira_connection(domain, email, api_token)
            else:
                raise ValueError(f"Unsupported provider: {provider}")
                
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error testing integration connection: {e}")
            raise
    
    def delete_integration_account(self, user_id: str, account_id: str, organization_id: Optional[str] = None) -> bool:
        """
        Delete an integration account.
        
        Args:
            user_id: User ID
            account_id: Integration account ID
            
        Returns:
            True if deleted successfully, False if not found
        """
        try:
            if organization_id:
                self._require_org_admin(str(organization_id), str(user_id))
            account = self.integrations_repo.get_integration_account(
                user_id,
                account_id,
                organization_id=organization_id,
            )
            if not account:
                return False

            if account.get("provider") == "slack":
                self.integrations_repo.delete_feedback_sources_by_account(account_id)

            return self.integrations_repo.delete_integration_account(
                user_id,
                account_id,
                organization_id=organization_id,
            )
        except Exception as e:
            logger.error(f"Error deleting integration account {account_id}: {e}")
            raise
    
    def get_external_projects(self, user_id: str, provider: str, organization_id: Optional[str] = None, **kwargs) -> List[Dict[str, Any]]:
        """
        Get projects from external platform API.
        
        Args:
            user_id: User ID
            provider: 'azure' or 'jira'
            **kwargs: Provider-specific parameters (organization, pat_token for Azure; domain, email, api_token for Jira)
                      OR accountId to fetch from stored integration account
            
        Returns:
            List of external projects
        """
        try:
            if organization_id:
                self._require_org_admin(str(organization_id), str(user_id))
            # Check if accountId is provided - if so, fetch credentials from database
            account_id = kwargs.get('accountId')
            if account_id:
                return self.get_external_projects_by_account(user_id, account_id, organization_id=organization_id)
            
            # Otherwise, use provided credentials directly
            if provider == 'azure':
                organization = kwargs.get('organization')
                pat_token = kwargs.get('pat_token')
                if not organization or not pat_token:
                    raise ValueError("Organization and PAT token are required for Azure")
                return self.external_api_service.fetch_azure_projects(organization, pat_token)
            elif provider == 'jira':
                domain = kwargs.get('domain')
                email = kwargs.get('email')
                api_token = kwargs.get('api_token')
                if not domain or not email or not api_token:
                    raise ValueError("Domain, email, and API token are required for Jira")
                return self.external_api_service.fetch_jira_projects(domain, email, api_token)
            else:
                raise ValueError(f"Unsupported provider: {provider}")
                
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error fetching external projects for {provider}: {e}")
            raise
    
    def get_external_projects_by_account(self, user_id: str, account_id: str, organization_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get projects from external platform API using stored integration account.
        
        Args:
            user_id: User ID
            account_id: Integration account ID
            
        Returns:
            List of external projects
        """
        try:
            if organization_id:
                self._require_org_admin(str(organization_id), str(user_id))
            # Get the integration account
            account = self.integrations_repo.get_integration_account(user_id, account_id, organization_id=organization_id)
            if not account:
                logger.error(f"Integration account {account_id} not found for user {user_id}")
                raise ValueError("Integration account not found")
            
            provider = account.get('provider')
            credentials = account.get('credentials', {})
            metadata = account.get('metadata', {})
            
            logger.info(f"Fetching projects for account {account_id}, provider: {provider}")
            logger.debug(f"Account metadata keys: {list(metadata.keys())}")
            logger.debug(f"Account credentials keys: {list(credentials.keys())}")
            
            # Decrypt credentials
            from .encryption_service import get_encryption_service
            encryption_service = get_encryption_service()
            
            if provider == 'azure':
                organization = metadata.get('organization')
                encrypted_pat = credentials.get('tokenEncrypted')
                if not organization or not encrypted_pat:
                    missing = []
                    if not organization:
                        missing.append('organization')
                    if not encrypted_pat:
                        missing.append('tokenEncrypted')
                    error_msg = f"Invalid Azure integration account: missing {', '.join(missing)}"
                    logger.error(f"{error_msg}. Metadata: {metadata}, Credentials keys: {list(credentials.keys())}")
                    raise ValueError(error_msg)
                pat_token = encryption_service.decrypt_token(encrypted_pat)
                return self.external_api_service.fetch_azure_projects(organization, pat_token)
            elif provider == 'jira':
                # Try multiple ways to get domain and email
                domain = metadata.get('domain') or account.get('domain')
                email = metadata.get('email') or credentials.get('email') or account.get('email')
                encrypted_token = credentials.get('tokenEncrypted') or credentials.get('token')
                
                # Log what we found for debugging
                logger.debug(f"Jira account check - domain: {domain}, email: {email}, has_token: {bool(encrypted_token)}")
                logger.debug(f"Full account structure - keys: {list(account.keys())}")
                
                if not domain or not email or not encrypted_token:
                    missing = []
                    if not domain:
                        missing.append('domain')
                    if not email:
                        missing.append('email')
                    if not encrypted_token:
                        missing.append('tokenEncrypted')
                    error_msg = f"Invalid Jira integration account: missing {', '.join(missing)}. Please reconfigure your Jira integration."
                    logger.error(f"{error_msg}. Account ID: {account_id}, Metadata keys: {list(metadata.keys())}, Credentials keys: {list(credentials.keys())}")
                    logger.error(f"Full account data: {account}")
                    raise ValueError(error_msg)
                
                api_token = encryption_service.decrypt_token(encrypted_token)
                return self.external_api_service.fetch_jira_projects(domain, email, api_token)
            else:
                raise ValueError(f"Unsupported provider: {provider}")
                
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error fetching external projects for account {account_id}: {e}", exc_info=True)
            raise
    
    def check_external_project_exists(
        self,
        provider: str,
        external_id: str,
        user_id: str,
        organization_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Check if an external project is already imported.
        
        Args:
            provider: 'azure' or 'jira'
            external_id: External project ID
            user_id: User ID
            
        Returns:
            Existing project data if found, None otherwise
        """
        try:
            return self.integrations_repo.check_external_project_exists(
                provider,
                external_id,
                user_id=user_id,
                organization_id=organization_id,
            )
        except Exception as e:
            logger.error(f"Error checking external project exists: {e}")
            raise


# Global service instance
_integration_service = None

def get_integration_service() -> IntegrationService:
    """Get the global integration service instance."""
    global _integration_service
    if _integration_service is None:
        _integration_service = IntegrationService()
    return _integration_service
