"""
External API service for interacting with external platforms.

This service handles the business logic for fetching data from external
platforms like Azure DevOps and Jira APIs, including connection testing.
"""

from typing import Dict, List, Optional, Any
import requests
import httpx
import base64
import logging

logger = logging.getLogger(__name__)


ASANA_API_BASE = "https://app.asana.com/api/1.0"


class ExternalApiService:
    """Service for external API interactions."""
    
    def normalize_jira_domain(self, domain: str) -> str:
        """Normalize Jira domain to ensure it has the correct format."""
        # Remove https:// if present
        domain = domain.replace('https://', '').replace('http://', '')
        
        # If domain already ends with .atlassian.net, return as is
        if domain.endswith('.atlassian.net'):
            return domain
        
        # If it's just the subdomain, add .atlassian.net
        return f"{domain}.atlassian.net"

    def build_jira_url(self, domain: str, path: str) -> str:
        """Build a proper Jira URL."""
        normalized_domain = self.normalize_jira_domain(domain)
        return f"https://{normalized_domain}{path}"

    def test_azure_connection(self, organization: str, pat_token: str) -> Dict[str, Any]:
        """Test Azure DevOps connection."""
        try:
            # Encode PAT token for Basic Auth
            credentials = base64.b64encode(f":{pat_token}".encode()).decode()
            
            headers = {
                'Authorization': f'Basic {credentials}',
                'Content-Type': 'application/json'
            }
            
            # Test with a simple API call to get organization info
            url = f"https://dev.azure.com/{organization}/_apis/projects?api-version=6.0&$top=1"
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                return {
                    'success': True,
                    'message': 'Connection successful',
                    'organization': organization
                }
            elif response.status_code == 401:
                return {
                    'success': False,
                    'error': 'Invalid PAT token or insufficient permissions'
                }
            elif response.status_code == 404:
                return {
                    'success': False,
                    'error': f'Organization "{organization}" not found'
                }
            else:
                return {
                    'success': False,
                    'error': f'Connection failed with status {response.status_code}'
                }
                
        except requests.exceptions.Timeout:
            return {
                'success': False,
                'error': 'Connection timeout - please check your network connection'
            }
        except requests.exceptions.RequestException as e:
            return {
                'success': False,
                'error': f'Network error: {str(e)}'
            }
        except Exception as e:
            logger.error(f"Error testing Azure connection: {e}")
            return {
                'success': False,
                'error': 'An unexpected error occurred'
            }

    def test_jira_connection(self, domain: str, email: str, api_token: str) -> Dict[str, Any]:
        """Test Jira connection."""
        try:
            # Encode credentials for Basic Auth
            credentials = base64.b64encode(f"{email}:{api_token}".encode()).decode()
            
            headers = {
                'Authorization': f'Basic {credentials}',
                'Content-Type': 'application/json'
            }
            
            # Test with a simple API call to get user info
            url = self.build_jira_url(domain, '/rest/api/3/myself')
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                user_data = response.json()
                return {
                    'success': True,
                    'message': 'Connection successful',
                    'domain': domain,
                    'user': user_data.get('displayName', email)
                }
            elif response.status_code == 401:
                return {
                    'success': False,
                    'error': 'Invalid email or API token'
                }
            elif response.status_code == 404:
                return {
                    'success': False,
                    'error': f'Jira instance "{domain}" not found'
                }
            else:
                return {
                    'success': False,
                    'error': f'Connection failed with status {response.status_code}'
                }
                
        except requests.exceptions.Timeout:
            return {
                'success': False,
                'error': 'Connection timeout - please check your network connection'
            }
        except requests.exceptions.RequestException as e:
            return {
                'success': False,
                'error': f'Network error: {str(e)}'
            }
        except Exception as e:
            logger.error(f"Error testing Jira connection: {e}")
            return {
                'success': False,
                'error': 'An unexpected error occurred'
            }
    
    def fetch_azure_projects(self, organization: str, pat_token: str) -> List[Dict[str, Any]]:
        """
        Fetch Azure DevOps projects directly from Azure API.
        
        Args:
            organization: Azure DevOps organization name
            pat_token: Personal Access Token
            
        Returns:
            List of Azure DevOps projects
            
        Raises:
            ValueError: If parameters are invalid
            Exception: If API call fails
        """
        try:
            if not organization or not pat_token:
                raise ValueError("Organization and PAT token are required")
            
            logger.info(f"Fetching Azure DevOps projects for organization: {organization}")
            
            # Encode PAT token for Basic Auth
            credentials = base64.b64encode(f":{pat_token}".encode()).decode()
            
            headers = {
                'Authorization': f'Basic {credentials}',
                'Content-Type': 'application/json'
            }
            
            # Fetch projects from Azure DevOps API
            url = f"https://dev.azure.com/{organization}/_apis/projects?api-version=6.0"
            
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                projects = data.get('value', [])
                
                # Normalize project data
                normalized_projects = []
                for project in projects:
                    normalized_projects.append({
                        'id': project.get('id'),
                        'name': project.get('name'),
                        'description': project.get('description', ''),
                        'url': project.get('url'),
                        'state': project.get('state'),
                        'visibility': project.get('visibility'),
                        'lastUpdateTime': project.get('lastUpdateTime')
                    })
                
                logger.info(f"Successfully fetched {len(normalized_projects)} Azure DevOps projects")
                return normalized_projects
            else:
                raise Exception(f"Azure API returned status {response.status_code}: {response.text}")
            
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error fetching Azure projects from API: {e}")
            raise Exception(f"Failed to fetch Azure DevOps projects: {str(e)}")

    def fetch_jira_projects(self, domain: str, email: str, api_token: str) -> List[Dict[str, Any]]:
        """
        Fetch Jira projects directly from Jira API.
        
        Args:
            domain: Jira domain
            email: User email
            api_token: Jira API token
            
        Returns:
            List of Jira projects
            
        Raises:
            ValueError: If parameters are invalid
            Exception: If API call fails
        """
        try:
            if not domain or not email or not api_token:
                raise ValueError("Domain, email, and API token are required")
            
            logger.info(f"Fetching Jira projects for domain: {domain}")
            
            # Encode credentials for Basic Auth
            credentials = base64.b64encode(f"{email}:{api_token}".encode()).decode()
            
            headers = {
                'Authorization': f'Basic {credentials}',
                'Content-Type': 'application/json'
            }
            
            # Fetch projects from Jira API
            url = self.build_jira_url(domain, '/rest/api/3/project')
            
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                projects = response.json()
                
                # Normalize project data
                normalized_projects = []
                for project in projects:
                    normalized_projects.append({
                        'id': project.get('id'),
                        'key': project.get('key'),
                        'name': project.get('name'),
                        'description': project.get('description', ''),
                        'projectTypeKey': project.get('projectTypeKey'),
                        'simplified': project.get('simplified'),
                        'style': project.get('style'),
                        'isPrivate': project.get('isPrivate'),
                        'url': project.get('self')
                    })
                
                logger.info(f"Successfully fetched {len(normalized_projects)} Jira projects")
                return normalized_projects
            else:
                raise Exception(f"Jira API returned status {response.status_code}: {response.text}")
            
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error fetching Jira projects from API: {e}")
            raise Exception(f"Failed to fetch Jira projects: {str(e)}")


    # ------------------------------------------------------------------
    # Asana (PAT-based, mirrors Jira/Azure)
    # ------------------------------------------------------------------

    def _asana_headers(self, pat_token: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {pat_token}",
            "Accept": "application/json",
        }

    def test_asana_connection(self, pat_token: str) -> Dict[str, Any]:
        """Test Asana connection by hitting GET /users/me."""
        try:
            if not pat_token:
                return {"success": False, "error": "PAT token is required"}

            response = httpx.get(
                f"{ASANA_API_BASE}/users/me",
                headers=self._asana_headers(pat_token),
                timeout=15,
            )

            if response.status_code == 200:
                user = response.json().get("data", {}) or {}
                return {
                    "success": True,
                    "message": "Connection successful",
                    "user": user.get("name", ""),
                    "user_gid": user.get("gid", ""),
                    "email": user.get("email", ""),
                }
            if response.status_code == 401:
                return {"success": False, "error": "Invalid Asana PAT or insufficient permissions"}
            if response.status_code == 402:
                return {
                    "success": False,
                    "error": "Asana workspace tier does not allow this operation. Upgrade required.",
                }
            return {
                "success": False,
                "error": f"Asana connection failed with status {response.status_code}",
            }

        except httpx.TimeoutException:
            return {"success": False, "error": "Connection timeout - check your network"}
        except httpx.RequestError as exc:
            return {"success": False, "error": f"Network error: {exc}"}
        except Exception as exc:
            logger.error(f"Error testing Asana connection: {exc}")
            return {"success": False, "error": "An unexpected error occurred"}

    def fetch_asana_workspaces(self, pat_token: str) -> List[Dict[str, Any]]:
        """List workspaces visible to the PAT user."""
        if not pat_token:
            raise ValueError("PAT token is required")

        items: List[Dict[str, Any]] = []
        offset: Optional[str] = None
        while True:
            params: Dict[str, Any] = {"limit": 100, "opt_fields": "name,resource_type,is_organization"}
            if offset:
                params["offset"] = offset

            response = httpx.get(
                f"{ASANA_API_BASE}/workspaces",
                headers=self._asana_headers(pat_token),
                params=params,
                timeout=30,
            )
            if response.status_code != 200:
                raise Exception(
                    f"Asana API returned status {response.status_code}: {response.text}"
                )
            body = response.json() or {}
            for ws in body.get("data", []) or []:
                items.append({
                    "gid": ws.get("gid"),
                    "name": ws.get("name", ""),
                    "is_organization": ws.get("is_organization", False),
                })
            next_page = body.get("next_page") or {}
            offset = next_page.get("offset") if next_page else None
            if not offset:
                break

        return items

    def fetch_asana_projects(self, pat_token: str, workspace_gid: str) -> List[Dict[str, Any]]:
        """List projects in a workspace, paginated."""
        if not pat_token or not workspace_gid:
            raise ValueError("PAT token and workspace GID are required")

        items: List[Dict[str, Any]] = []
        offset: Optional[str] = None
        while True:
            params: Dict[str, Any] = {
                "workspace": workspace_gid,
                "limit": 100,
                "opt_fields": "name,resource_type,archived,team.name",
                "archived": "false",
            }
            if offset:
                params["offset"] = offset

            response = httpx.get(
                f"{ASANA_API_BASE}/projects",
                headers=self._asana_headers(pat_token),
                params=params,
                timeout=30,
            )
            if response.status_code != 200:
                raise Exception(
                    f"Asana API returned status {response.status_code}: {response.text}"
                )
            body = response.json() or {}
            for p in body.get("data", []) or []:
                items.append({
                    "gid": p.get("gid"),
                    "id": p.get("gid"),
                    "name": p.get("name", ""),
                    "archived": p.get("archived", False),
                    "team": (p.get("team") or {}).get("name", ""),
                    "url": f"https://app.asana.com/0/{p.get('gid')}/list",
                })
            next_page = body.get("next_page") or {}
            offset = next_page.get("offset") if next_page else None
            if not offset:
                break

        return items


# Global service instance
_external_api_service = None

def get_external_api_service() -> ExternalApiService:
    """Get the global external API service instance."""
    global _external_api_service
    if _external_api_service is None:
        _external_api_service = ExternalApiService()
    return _external_api_service


# Legacy function wrappers for backward compatibility
def test_azure_connection(organization: str, pat_token: str) -> Dict[str, Any]:
    """Legacy wrapper - use get_external_api_service().test_azure_connection() instead."""
    return get_external_api_service().test_azure_connection(organization, pat_token)

def test_jira_connection(domain: str, email: str, api_token: str) -> Dict[str, Any]:
    """Legacy wrapper - use get_external_api_service().test_jira_connection() instead."""
    return get_external_api_service().test_jira_connection(domain, email, api_token)

def fetch_azure_projects(organization: str, pat_token: str) -> List[Dict[str, Any]]:
    """Legacy wrapper - use get_external_api_service().fetch_azure_projects() instead."""
    return get_external_api_service().fetch_azure_projects(organization, pat_token)

def fetch_jira_projects(domain: str, email: str, api_token: str) -> List[Dict[str, Any]]:
    """Legacy wrapper - use get_external_api_service().fetch_jira_projects() instead."""
    return get_external_api_service().fetch_jira_projects(domain, email, api_token)