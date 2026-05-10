"""
Slack integration service for OAuth, channel listing, and message fetching.

Handles the full Slack integration lifecycle:
- OAuth bot installation flow
- Channel listing and connection testing
- Message fetching with pagination and rate limiting
- Normalization to Saramsa feedback format
- Deduplication of already-synced messages
"""

import time
import uuid
import logging
import requests
from typing import Dict, List, Optional, Any, Set
from datetime import datetime, timedelta, timezone

from django.conf import settings
from ..repositories import IntegrationsRepository
from .encryption_service import get_encryption_service

logger = logging.getLogger(__name__)

SLACK_OAUTH_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_OAUTH_ACCESS_URL = "https://slack.com/api/oauth.v2.access"
SLACK_AUTH_TEST_URL = "https://slack.com/api/auth.test"
SLACK_CONVERSATIONS_LIST_URL = "https://slack.com/api/conversations.list"
SLACK_CONVERSATIONS_HISTORY_URL = "https://slack.com/api/conversations.history"
SLACK_USERS_INFO_URL = "https://slack.com/api/users.info"

SLACK_BOT_SCOPES = "channels:history,channels:read,groups:history,groups:read,users:read"


class SlackService:
    """Service for Slack integration business logic."""

    def __init__(self):
        self.integrations_repo = IntegrationsRepository()
        self.encryption_service = get_encryption_service()

    # ------------------------------------------------------------------
    # OAuth helpers
    # ------------------------------------------------------------------

    def start_oauth(self, user_id: str, organization_id: Optional[str] = None) -> str:
        """Generate Slack OAuth URL and persist the state token."""
        client_id = getattr(settings, "SLACK_CLIENT_ID", "") or ""
        redirect_uri = getattr(settings, "SLACK_REDIRECT_URI", "") or ""
        team_id = getattr(settings, "SLACK_TEAM_ID", "") or ""
        if not client_id:
            raise ValueError("Slack OAuth is not configured: SLACK_CLIENT_ID is missing.")
        if not redirect_uri:
            raise ValueError("Slack OAuth is not configured: SLACK_REDIRECT_URI is missing.")

        state = f"slack_{uuid.uuid4().hex}"

        # Persist state in ORM for callback validation (15 minutes TTL).
        self.integrations_repo.create_oauth_state(
            state_id=state,
            user_id=user_id,
            provider="slack",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            metadata={"organizationId": str(organization_id)} if organization_id else {},
        )

        oauth_url = (
            f"{SLACK_OAUTH_AUTHORIZE_URL}"
            f"?client_id={client_id}"
            f"&scope={SLACK_BOT_SCOPES}"
            f"&redirect_uri={redirect_uri}"
            f"&state={state}"
        )
        if team_id:
            oauth_url += f"&team={team_id}"
        return oauth_url

    def complete_oauth(self, code: str, state: str) -> Dict[str, Any]:
        """Exchange authorization code for a bot token and store it."""
        # Validate state
        state_doc = self.integrations_repo.get_oauth_state(state)

        if not state_doc:
            raise ValueError("Invalid or expired OAuth state")

        user_id = state_doc["userId"]

        # Exchange code for token
        client_id = getattr(settings, "SLACK_CLIENT_ID", "") or ""
        client_secret = getattr(settings, "SLACK_CLIENT_SECRET", "") or ""
        redirect_uri = getattr(settings, "SLACK_REDIRECT_URI", "") or ""
        if not client_id or not client_secret or not redirect_uri:
            raise ValueError(
                "Slack OAuth is not configured: set SLACK_CLIENT_ID, SLACK_CLIENT_SECRET, and SLACK_REDIRECT_URI."
            )

        resp = requests.post(
            SLACK_OAUTH_ACCESS_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=30,
        )
        data = resp.json()

        if not data.get("ok"):
            error = data.get("error", "unknown_error")
            raise ValueError(f"Slack OAuth failed: {error}")

        bot_token = data.get("access_token", "")
        team_info = data.get("team", {})
        team_name = team_info.get("name", "Slack Workspace")
        team_id = team_info.get("id", "")

        # Encrypt and store
        encrypted_token = self.encryption_service.encrypt_token(bot_token)

        account_id = f"ia_{uuid.uuid4().hex[:12]}"
        organization_id = (state_doc.get("metadata") or {}).get("organizationId")

        account_doc = {
            "id": account_id,
            "type": "integration_account",
            "userId": user_id,
            "organizationId": organization_id,
            "provider": "slack",
            "displayName": f"{team_name} (Slack)",
            "status": "active",
            "credentials": {
                "tokenEncrypted": encrypted_token,
                "tokenType": "bot",
            },
            "metadata": {
                "teamId": team_id,
                "teamName": team_name,
            },
            "scopes": SLACK_BOT_SCOPES.split(","),
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "schemaVersion": 1,
        }

        result = self.integrations_repo.create_or_update_integration_account(account_doc)

        # Clean up state document (best-effort)
        try:
            self.integrations_repo.delete_oauth_state(state)
        except Exception:
            pass

        return result

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    def _get_bot_token(self, user_id: str, account_id: str, organization_id: Optional[str] = None) -> str:
        """Retrieve and decrypt the bot token for an account."""
        account = self.integrations_repo.get_integration_account(
            user_id,
            account_id,
            organization_id=organization_id,
        )
        if not account:
            raise ValueError("Slack integration account not found")
        encrypted = account.get("credentials", {}).get("tokenEncrypted", "")
        if not encrypted:
            raise ValueError("No token stored for this Slack account")
        return self.encryption_service.decrypt_token(encrypted)

    def test_connection(self, user_id: str, account_id: str, organization_id: Optional[str] = None) -> Dict[str, Any]:
        """Test connection using stored bot token."""
        try:
            token = self._get_bot_token(user_id, account_id, organization_id=organization_id)
            resp = requests.post(
                SLACK_AUTH_TEST_URL,
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            data = resp.json()
            if data.get("ok"):
                return {
                    "success": True,
                    "team": data.get("team"),
                    "user": data.get("user"),
                    "url": data.get("url"),
                }
            return {"success": False, "error": data.get("error", "auth.test failed")}
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Slack connection test failed: {e}")
            return {"success": False, "error": str(e)}

    def list_channels(self, user_id: str, account_id: str, organization_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List conversations the bot can see."""
        token = self._get_bot_token(user_id, account_id, organization_id=organization_id)
        channels: List[Dict[str, Any]] = []
        cursor = None

        while True:
            params: Dict[str, Any] = {
                "types": "public_channel,private_channel",
                "exclude_archived": "true",
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor

            resp = requests.get(
                SLACK_CONVERSATIONS_LIST_URL,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=15,
            )
            data = resp.json()
            if not data.get("ok"):
                raise ValueError(f"Failed to list channels: {data.get('error')}")

            for ch in data.get("channels", []):
                channels.append({
                    "id": ch["id"],
                    "name": ch.get("name", ""),
                    "is_private": ch.get("is_private", False),
                    "num_members": ch.get("num_members", 0),
                })

            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
            time.sleep(1.2)  # rate-limit courtesy

        return channels

    # ------------------------------------------------------------------
    # Message fetching  (SAR-40)
    # ------------------------------------------------------------------

    def fetch_channel_messages(
        self,
        user_id: str,
        account_id: str,
        channel_id: str,
        oldest: Optional[str] = None,
        organization_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch messages from a channel, handling pagination and rate limits."""
        token = self._get_bot_token(user_id, account_id, organization_id=organization_id)
        messages: List[Dict[str, Any]] = []
        cursor = None

        while True:
            params: Dict[str, Any] = {"channel": channel_id, "limit": 200}
            if oldest:
                params["oldest"] = oldest
            if cursor:
                params["cursor"] = cursor

            resp = self._slack_api_get(
                SLACK_CONVERSATIONS_HISTORY_URL, token, params
            )
            data = resp.json()

            if not data.get("ok"):
                error = data.get("error", "unknown")
                logger.error(f"conversations.history failed for {channel_id}: {error}")
                break

            for msg in data.get("messages", []):
                # Skip bot messages and empty messages
                if msg.get("subtype") or msg.get("bot_id"):
                    continue
                if not msg.get("text", "").strip():
                    continue

                messages.append({
                    "slack_ts": msg["ts"],
                    "text": msg["text"],
                    "user_id": msg.get("user", ""),
                    "channel_id": channel_id,
                    "timestamp": msg["ts"],
                })

            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
            time.sleep(1.2)

        return messages

    def resolve_user_names(
        self, user_ids: List[str], bot_token: str
    ) -> Dict[str, str]:
        """Resolve Slack user IDs to display names (with in-memory cache)."""
        cache: Dict[str, str] = {}
        unique_ids = set(user_ids)
        for uid in unique_ids:
            if uid in cache:
                continue
            try:
                resp = requests.get(
                    SLACK_USERS_INFO_URL,
                    headers={"Authorization": f"Bearer {bot_token}"},
                    params={"user": uid},
                    timeout=10,
                )
                data = resp.json()
                if data.get("ok"):
                    profile = data["user"].get("profile", {})
                    name = (
                        profile.get("display_name")
                        or profile.get("real_name")
                        or data["user"].get("name", uid)
                    )
                    cache[uid] = name
                else:
                    cache[uid] = uid
                time.sleep(1.2)
            except Exception:
                cache[uid] = uid
        return cache

    def normalize_to_feedback(
        self,
        messages: List[Dict[str, Any]],
        channel_name: str,
        user_names: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """Convert raw Slack messages into Saramsa feedback format."""
        feedback: List[Dict[str, Any]] = []
        for msg in messages:
            ts_float = float(msg["slack_ts"])
            created_at = datetime.fromtimestamp(ts_float, tz=timezone.utc).isoformat()
            author = (user_names or {}).get(msg.get("user_id", ""), msg.get("user_id", "unknown"))

            feedback.append({
                "comment": msg["text"],
                "source": "slack",
                "source_id": f"{msg['channel_id']}_{msg['slack_ts']}",
                "source_channel": channel_name,
                "author": author,
                "created_at": created_at,
            })
        return feedback

    # ------------------------------------------------------------------
    # Deduplication  (SAR-44)
    # ------------------------------------------------------------------

    def deduplicate_messages(
        self,
        messages: List[Dict[str, Any]],
        existing_source_ids: Set[str],
    ) -> List[Dict[str, Any]]:
        """Filter out messages whose source_id already exists."""
        new_messages = [
            m for m in messages
            if f"{m['channel_id']}_{m['slack_ts']}" not in existing_source_ids
        ]
        removed = len(messages) - len(new_messages)
        if removed:
            logger.info(f"Deduped {removed} duplicates, {len(new_messages)} new")
        return new_messages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _slack_api_get(
        self, url: str, token: str, params: Dict[str, Any]
    ) -> requests.Response:
        """GET with automatic retry on 429."""
        for attempt in range(3):
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=30,
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "5"))
                logger.warning(f"Slack rate limited, retrying after {retry_after}s")
                time.sleep(retry_after)
                continue
            return resp
        return resp  # return last response even if still 429


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------
_slack_service: Optional[SlackService] = None


def get_slack_service() -> SlackService:
    """Get the global SlackService instance."""
    global _slack_service
    if _slack_service is None:
        _slack_service = SlackService()
    return _slack_service
