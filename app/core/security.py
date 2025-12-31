"""
Security Module

Rate limiting, encryption, and security utilities.
"""

import os
import base64
import logging
from functools import lru_cache
from typing import Optional

from slowapi import Limiter
from slowapi.util import get_remote_address
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)


# =============================================================================
# Rate Limiting
# =============================================================================

def get_client_ip(request) -> str:
    """
    Get client IP address from request.
    Handles X-Forwarded-For header for reverse proxies (Railway, etc.)
    """
    # Check for forwarded header (Railway, nginx, etc.)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # X-Forwarded-For can contain multiple IPs; first is the client
        return forwarded.split(",")[0].strip()
    
    # Check for real IP header
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    
    # Fall back to direct connection IP
    return get_remote_address(request)


# Create rate limiter instance
limiter = Limiter(
    key_func=get_client_ip,
    default_limits=["200 per minute"],  # Global default
    storage_uri="memory://",  # In-memory storage (use Redis for multi-instance)
    strategy="fixed-window",
)

# Rate limit configurations for different endpoint types
RATE_LIMITS = {
    "login": "5 per minute",           # Strict: prevent brute force
    "register": "3 per minute",        # Very strict: prevent spam
    "password_reset": "3 per minute",  # Strict: prevent abuse
    "trades": "30 per minute",         # Moderate: allow trading activity
    "api_general": "100 per minute",   # General API calls
    "admin": "50 per minute",          # Admin operations
}


# =============================================================================
# Encryption for Sensitive Data (SnapTrade secrets, etc.)
# =============================================================================

@lru_cache()
def get_encryption_key() -> bytes:
    """
    Get or derive encryption key from environment variable.
    Uses PBKDF2 to derive a proper Fernet key from the secret.
    """
    secret = os.getenv("ENCRYPTION_KEY")
    
    if not secret:
        logger.warning(
            "ENCRYPTION_KEY not set! Using fallback key. "
            "SET THIS IN PRODUCTION!"
        )
        # Fallback for development only - NOT SECURE FOR PRODUCTION
        secret = "dev-fallback-key-not-for-production"
    
    # Use a fixed salt (in production, you might want to store this separately)
    salt = b"portfolio-management-salt-v1"
    
    # Derive a proper 32-byte key using PBKDF2
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    
    key = base64.urlsafe_b64encode(kdf.derive(secret.encode()))
    return key


def get_fernet() -> Fernet:
    """Get Fernet instance for encryption/decryption."""
    return Fernet(get_encryption_key())


def encrypt_value(plaintext: str) -> str:
    """
    Encrypt a string value.
    Returns base64-encoded encrypted string.
    """
    if not plaintext:
        return plaintext
    
    try:
        fernet = get_fernet()
        encrypted = fernet.encrypt(plaintext.encode())
        return encrypted.decode()
    except Exception as e:
        logger.error(f"Encryption failed: {e}")
        raise ValueError("Failed to encrypt value")


def decrypt_value(encrypted: str) -> str:
    """
    Decrypt an encrypted string value.
    Returns original plaintext.
    """
    if not encrypted:
        return encrypted
    
    try:
        fernet = get_fernet()
        decrypted = fernet.decrypt(encrypted.encode())
        return decrypted.decode()
    except Exception as e:
        logger.error(f"Decryption failed: {e}")
        raise ValueError("Failed to decrypt value")


def is_encrypted(value: str) -> bool:
    """
    Check if a value appears to be encrypted (Fernet format).
    Fernet tokens start with 'gAAAAA'.
    """
    if not value:
        return False
    return value.startswith("gAAAAA")


# =============================================================================
# Security Headers Middleware
# =============================================================================

SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "X-XSS-Protection": "1; mode=block",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


async def add_security_headers(request, call_next):
    """Middleware to add security headers to all responses."""
    response = await call_next(request)
    
    for header, value in SECURITY_HEADERS.items():
        response.headers[header] = value
    
    return response
