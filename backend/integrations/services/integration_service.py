"""
Integration service for managing external platform integrations.

This service handles the business logic for creating, managing, and testing
integrations with external platforms like Azure DevOps and Jira.
"""

from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import uuid
import logging

from django.db import transaction

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

    def _require_account_admin(self, user_id: str, account_id: str) -> Optional[str]:
        """Authorize access to an existing account by its OWN organization.

        Resolves the account's owning organization and requires the caller be an
        admin of that organization. Returns the account's organization id, or
        None if the account does not exist. Deriving the org from the account
        (rather than the caller's active org) closes the gap where a missing
        active-org context skipped the admin check.
        """
        account_org_id = self.integrations_repo.get_account_organization_id(account_id)
        if not account_org_id:
            return None
        self._require_org_admin(account_org_id, user_id)
        return account_org_id

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
            "jira": ["read:project", "write:issue", "read:issue"],
            "asana": ["tasks:read", "tasks:write", "projects:read", "users:read"],
            "linear": ["read", "write"],
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
        
        if account_data["provider"] not in ["azure", "jira", "asana", "linear"]:
            raise ValueError("Provider must be 'azure', 'jira', 'asana', or 'linear'")
        
        return True
    
    def get_integration_accounts_by_user(self, user_id: str, organization_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all integration accounts for a user within an organization."""
        try:
            if organization_id:
                self.organization_service.require_membership(str(organization_id), str(user_id))
                return self.integrations_repo.get_by_organization(str(organization_id))
            return self.integrations_repo.get_integration_accounts_by_user(user_id)
        except ValueError:
            # Expected domain rejection (e.g. not a member); the view maps it to a
            # 4xx. Re-raise without a stack-trace log.
            raise
        except Exception:
            logger.exception("Failed to get integration accounts", extra={"user_id": user_id})
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
        elif provider == "asana":
            decrypted_credentials["pat_token"] = decrypted_token
        elif provider == "linear":
            decrypted_credentials["api_key"] = decrypted_token
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
            logger.exception("Failed to create Azure integration")
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
            logger.exception("Failed to create Jira integration")
            raise
    
    def create_asana_integration(
        self,
        user_id: str,
        organization_id: str,
        pat_token: str,
        workspace_gid: str,
        workspace_name: str = "",
    ) -> Dict[str, Any]:
        """Create or update an Asana integration account.

        Mirrors the Azure/Jira PAT pattern. Validates the PAT against
        GET /users/me before persisting. Customer-supplied workspace_gid
        scopes the integration to one Asana workspace.
        """
        try:
            if not organization_id or not pat_token or not workspace_gid:
                raise ValueError("Organization ID, PAT token, and workspace GID are required")

            self._require_org_admin(str(organization_id), str(user_id))

            test_result = self.external_api_service.test_asana_connection(pat_token)
            if not test_result["success"]:
                raise ValueError(f"Connection test failed: {test_result['error']}")

            from .encryption_service import get_encryption_service
            encryption_service = get_encryption_service()
            encrypted_token = encryption_service.encrypt_token(pat_token)

            credentials = {
                "tokenEncrypted": encrypted_token,
                "tokenType": "pat",
            }
            metadata = {
                "workspaceGid": workspace_gid,
                "workspaceName": workspace_name,
                "userGid": test_result.get("user_gid", ""),
                "userName": test_result.get("user", ""),
                "baseUrl": "https://app.asana.com",
            }

            display_name = f"{workspace_name or 'Asana'} (Asana)"
            account_data = self._create_integration_account_document(
                user_id=user_id,
                organization_id=organization_id,
                provider="asana",
                credentials=credentials,
                metadata=metadata,
                display_name=display_name,
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
            logger.exception("Failed to create Asana integration")
            raise

    def create_linear_integration(
        self,
        user_id: str,
        organization_id: str,
        api_key: str,
        workspace_name: str = "",
    ) -> Dict[str, Any]:
        """Create or update a Linear integration account.

        Mirrors the Asana PAT pattern. Validates the API key against
        Linear's `viewer` query before persisting.
        """
        try:
            if not organization_id or not api_key:
                raise ValueError("Organization ID and API key are required")

            self._require_org_admin(str(organization_id), str(user_id))

            test_result = self.external_api_service.test_linear_connection(api_key)
            if not test_result["success"]:
                raise ValueError(f"Connection test failed: {test_result['error']}")

            from .encryption_service import get_encryption_service
            encryption_service = get_encryption_service()
            encrypted_token = encryption_service.encrypt_token(api_key)

            credentials = {
                "tokenEncrypted": encrypted_token,
                "tokenType": "api_key",
            }
            resolved_workspace_name = workspace_name or test_result.get("workspace_name", "")
            metadata = {
                "workspaceId": test_result.get("workspace_id", ""),
                "workspaceName": resolved_workspace_name,
                "workspaceUrlKey": test_result.get("workspace_url_key", ""),
                "userId": test_result.get("user_id", ""),
                "userName": test_result.get("user", ""),
                "email": test_result.get("email", ""),
                "baseUrl": "https://linear.app",
            }

            display_name = f"{resolved_workspace_name or 'Linear'} (Linear)"
            account_data = self._create_integration_account_document(
                user_id=user_id,
                organization_id=organization_id,
                provider="linear",
                credentials=credentials,
                metadata=metadata,
                display_name=display_name,
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
            logger.exception("Failed to create Linear integration")
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
            # Authorize against the account's own organization, then load it.
            account_org_id = self._require_account_admin(user_id, account_id)
            if not account_org_id:
                raise ValueError("Integration account not found")
            account = self.integrations_repo.get_integration_account(user_id, account_id, organization_id=account_org_id)
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
            elif provider == 'asana':
                encrypted_token = credentials.get('tokenEncrypted')
                pat_token = encryption_service.decrypt_token(encrypted_token)
                return self.external_api_service.test_asana_connection(pat_token)
            elif provider == 'linear':
                encrypted_token = credentials.get('tokenEncrypted')
                api_key = encryption_service.decrypt_token(encrypted_token)
                return self.external_api_service.test_linear_connection(api_key)
            else:
                raise ValueError(f"Unsupported provider: {provider}")

        except ValueError:
            raise
        except Exception as e:
            logger.exception("Failed to test integration connection")
            raise
    
    def delete_integration_account(self, user_id: str, account_id: str, organization_id: Optional[str] = None) -> bool:
        """
        Delete an integration account.

        Wraps the deletion in a database transaction so that any related
        cleanup (e.g. feedback sources tied to this account) and the
        account row itself either all succeed or all roll back together.

        Args:
            user_id: User ID
            account_id: Integration account ID

        Returns:
            True if deleted successfully, False if not found
        """
        try:
            with transaction.atomic():
                account_org_id = self._require_account_admin(user_id, account_id)
                if not account_org_id:
                    return False
                account = self.integrations_repo.get_integration_account(
                    user_id,
                    account_id,
                    organization_id=account_org_id,
                )
                if not account:
                    return False

                if account.get("provider") == "slack":
                    self.integrations_repo.delete_feedback_sources_by_account(account_id)

                return self.integrations_repo.delete_integration_account(
                    user_id,
                    account_id,
                    organization_id=account_org_id,
                )
        except ValueError:
            # Expected domain rejection (e.g. not an admin / no org access); the
            # view turns it into a 4xx. Re-raise without a stack-trace log.
            raise
        except Exception:
            logger.exception("Failed to delete integration account", extra={"account_id": account_id})
            raise
    
    def get_external_projects(self, user_id: str, provider: str, organization_id: Optional[str] = None, **kwargs) -> List[Dict[str, Any]]:
        """
        Get projects from external platform API.
        
        Args:
            user_id: User ID
            provider: 'azure', 'jira', 'asana', or 'linear'
            **kwargs: Provider-specific parameters
                      (organization, pat_token for Azure;
                       domain, email, api_token for Jira;
                       pat_token, workspace_gid for Asana)
                      OR accountId to fetch from stored integration account
            
        Returns:
            List of external projects
        """
        try:
            # Account-scoped path authorizes against the account's own org.
            account_id = kwargs.get('accountId')
            if account_id:
                return self.get_external_projects_by_account(user_id, account_id, organization_id=organization_id)

            # Direct-credentials path (pre-save): gate on the caller's active org.
            if organization_id:
                self._require_org_admin(str(organization_id), str(user_id))

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
            elif provider == 'asana':
                pat_token = kwargs.get('pat_token')
                workspace_gid = kwargs.get('workspace_gid')
                if not pat_token or not workspace_gid:
                    raise ValueError("PAT token and workspace GID are required for Asana")
                return self.external_api_service.fetch_asana_projects(pat_token, workspace_gid)
            elif provider == 'linear':
                api_key = kwargs.get('api_key')
                if not api_key:
                    raise ValueError("API key is required for Linear")
                return self.external_api_service.fetch_linear_teams(api_key)
            else:
                raise ValueError(f"Unsupported provider: {provider}")

        except ValueError:
            raise
        except Exception as e:
            logger.exception("Failed to fetch external projects", extra={"provider": provider})
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
            # Authorize against the account's own organization, then load it.
            account_org_id = self._require_account_admin(user_id, account_id)
            if not account_org_id:
                logger.error("Integration account not found", extra={"account_id": account_id, "user_id": user_id})
                raise ValueError("Integration account not found")
            account = self.integrations_repo.get_integration_account(user_id, account_id, organization_id=account_org_id)
            if not account:
                logger.error("Integration account not found", extra={"account_id": account_id, "user_id": user_id})
                raise ValueError("Integration account not found")
            
            provider = account.get('provider')
            credentials = account.get('credentials', {})
            metadata = account.get('metadata', {})
            
            logger.debug("Fetching projects for account", extra={"account_id": account_id, "provider": provider})
            logger.debug("Account metadata keys", extra={"metadata_keys": list(metadata.keys())})
            logger.debug("Account credential keys", extra={"credential_keys": list(credentials.keys())})
            
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
                    logger.error("Invalid Azure integration account", extra={"missing": missing, "metadata_keys": list(metadata.keys()), "credential_keys": list(credentials.keys())})
                    raise ValueError(error_msg)
                pat_token = encryption_service.decrypt_token(encrypted_pat)
                return self.external_api_service.fetch_azure_projects(organization, pat_token)
            elif provider == 'jira':
                # Try multiple ways to get domain and email
                domain = metadata.get('domain') or account.get('domain')
                email = metadata.get('email') or credentials.get('email') or account.get('email')
                encrypted_token = credentials.get('tokenEncrypted') or credentials.get('token')
                
                # Log what we found for debugging
                logger.debug("Jira account check", extra={"domain": domain, "has_email": bool(email), "has_token": bool(encrypted_token)})
                logger.debug("Jira account structure", extra={"account_keys": list(account.keys())})
                
                if not domain or not email or not encrypted_token:
                    missing = []
                    if not domain:
                        missing.append('domain')
                    if not email:
                        missing.append('email')
                    if not encrypted_token:
                        missing.append('tokenEncrypted')
                    error_msg = f"Invalid Jira integration account: missing {', '.join(missing)}. Please reconfigure your Jira integration."
                    logger.error("Invalid Jira integration account", extra={"account_id": account_id, "missing": missing, "metadata_keys": list(metadata.keys()), "credential_keys": list(credentials.keys())})
                    raise ValueError(error_msg)
                
                api_token = encryption_service.decrypt_token(encrypted_token)
                return self.external_api_service.fetch_jira_projects(domain, email, api_token)
            elif provider == 'asana':
                workspace_gid = metadata.get('workspaceGid')
                encrypted_token = credentials.get('tokenEncrypted')
                if not workspace_gid or not encrypted_token:
                    missing = []
                    if not workspace_gid:
                        missing.append('workspaceGid')
                    if not encrypted_token:
                        missing.append('tokenEncrypted')
                    raise ValueError(
                        f"Invalid Asana integration account: missing {', '.join(missing)}"
                    )
                pat_token = encryption_service.decrypt_token(encrypted_token)
                return self.external_api_service.fetch_asana_projects(pat_token, workspace_gid)
            elif provider == 'linear':
                encrypted_token = credentials.get('tokenEncrypted')
                if not encrypted_token:
                    raise ValueError(
                        "Invalid Linear integration account: missing tokenEncrypted"
                    )
                api_key = encryption_service.decrypt_token(encrypted_token)
                return self.external_api_service.fetch_linear_teams(api_key)
            else:
                raise ValueError(f"Unsupported provider: {provider}")

        except ValueError:
            raise
        except Exception as e:
            logger.exception("Failed to fetch external projects for account", extra={"account_id": account_id})
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
            provider: 'azure', 'jira', 'asana', or 'linear'
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
            logger.exception("Failed to check whether external project exists")
            raise


# Global service instance
_integration_service = None

def get_integration_service() -> IntegrationService:
    """Get the global integration service instance."""
    global _integration_service
    if _integration_service is None:
        _integration_service = IntegrationService()
    return _integration_service
