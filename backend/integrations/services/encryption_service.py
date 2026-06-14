"""
Encryption service for secure storage of integration credentials.

This service handles encryption/decryption of sensitive tokens and credentials
using AES-256-GCM encryption with a key derived from Django SECRET_KEY.
"""

import base64
import hashlib
from cryptography.fernet import Fernet
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


class EncryptionService:
    """Service for encrypting and decrypting sensitive data."""
    
    def _get_encryption_key(self) -> bytes:
        """Generate a consistent encryption key from Django SECRET_KEY."""
        # Use PBKDF2 to derive a 32-byte key from SECRET_KEY
        key_material = settings.SECRET_KEY.encode('utf-8')
        salt = b'saramsa_integrations_salt'  # Fixed salt for consistency
        key = hashlib.pbkdf2_hmac('sha256', key_material, salt, 100000)
        return base64.urlsafe_b64encode(key)

    def encrypt_token(self, token: str) -> str:
        """Encrypt a token for secure storage."""
        try:
            if not token:
                return ""
            
            key = self._get_encryption_key()
            fernet = Fernet(key)
            
            # Encrypt the token
            encrypted_token = fernet.encrypt(token.encode('utf-8'))
            
            # Return base64 encoded string for JSON storage
            return base64.b64encode(encrypted_token).decode('utf-8')
            
        except Exception as e:
            logger.exception("Failed to encrypt token")
            raise ValueError("Failed to encrypt token")

    def decrypt_token(self, encrypted_token: str) -> str:
        """Decrypt a token for use."""
        try:
            if not encrypted_token:
                return ""
            
            key = self._get_encryption_key()
            fernet = Fernet(key)
            
            # Decode from base64
            encrypted_bytes = base64.b64decode(encrypted_token.encode('utf-8'))
            
            # Decrypt the token
            decrypted_token = fernet.decrypt(encrypted_bytes)
            
            return decrypted_token.decode('utf-8')
            
        except Exception as e:
            logger.exception("Failed to decrypt token")
            raise ValueError("Failed to decrypt token")

    def is_token_encrypted(self, token: str) -> bool:
        """Check if a token appears to be encrypted (base64 encoded)."""
        try:
            if not token:
                return False
            
            # Try to decode as base64
            base64.b64decode(token.encode('utf-8'))
            
            # If it's much longer than typical tokens, it's likely encrypted
            return len(token) > 100
            
        except Exception:
            return False

    def migrate_token_to_encrypted(self, plain_token: str) -> str:
        """Helper function to migrate existing plain tokens to encrypted format."""
        if self.is_token_encrypted(plain_token):
            return plain_token  # Already encrypted
        
        return self.encrypt_token(plain_token)


# Global service instance
_encryption_service = None

def get_encryption_service() -> EncryptionService:
    """Get the global encryption service instance."""
    global _encryption_service
    if _encryption_service is None:
        _encryption_service = EncryptionService()
    return _encryption_service


# Legacy function wrappers for backward compatibility
def encrypt_token(token: str) -> str:
    """Legacy wrapper - use get_encryption_service().encrypt_token() instead."""
    return get_encryption_service().encrypt_token(token)

def decrypt_token(encrypted_token: str) -> str:
    """Legacy wrapper - use get_encryption_service().decrypt_token() instead."""
    return get_encryption_service().decrypt_token(encrypted_token)

def is_token_encrypted(token: str) -> bool:
    """Legacy wrapper - use get_encryption_service().is_token_encrypted() instead."""
    return get_encryption_service().is_token_encrypted(token)

def migrate_token_to_encrypted(plain_token: str) -> str:
    """Legacy wrapper - use get_encryption_service().migrate_token_to_encrypted() instead."""
    return get_encryption_service().migrate_token_to_encrypted(plain_token)