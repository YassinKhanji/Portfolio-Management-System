"""
Logging Configuration

Centralized logging setup with Log model integration and sensitive data sanitization.
"""

import logging
from logging.handlers import RotatingFileHandler
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Pattern

# Create logs directory if it doesn't exist
LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(exist_ok=True)


# ============================================================================
# SENSITIVE DATA SANITIZATION
# ============================================================================

# Patterns for sensitive data that should be redacted from logs
SENSITIVE_PATTERNS: List[tuple[Pattern, str]] = [
    # UUIDs - user_id, account_id, connection_id, etc.
    (re.compile(r'\b([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\b', re.IGNORECASE), '[REDACTED_ID]'),
    
    # SnapTrade user IDs (alphanumeric, typically 20+ chars)
    (re.compile(r'(snaptrade_user_id[=:\s]+)([A-Za-z0-9_-]{10,})', re.IGNORECASE), r'\1[REDACTED]'),
    
    # User secrets and tokens
    (re.compile(r'(user_secret[=:\s]+)([^\s,}\]]+)', re.IGNORECASE), r'\1[REDACTED]'),
    (re.compile(r'(snaptrade_token[=:\s]+)([^\s,}\]]+)', re.IGNORECASE), r'\1[REDACTED]'),
    (re.compile(r'(userSecret[=:\s]+)([^\s,}\]&]+)', re.IGNORECASE), r'\1[REDACTED]'),
    
    # API keys and secrets
    (re.compile(r'(api_key[=:\s]+)([^\s,}\]]+)', re.IGNORECASE), r'\1[REDACTED]'),
    (re.compile(r'(apikey[=:\s]+)([^\s,}\]&]+)', re.IGNORECASE), r'\1[REDACTED]'),
    (re.compile(r'(secret[=:\s]+)([^\s,}\]]+)', re.IGNORECASE), r'\1[REDACTED]'),
    (re.compile(r'(consumer_key[=:\s]+)([^\s,}\]]+)', re.IGNORECASE), r'\1[REDACTED]'),
    (re.compile(r'(client_secret[=:\s]+)([^\s,}\]]+)', re.IGNORECASE), r'\1[REDACTED]'),
    
    # JWT tokens (Bearer tokens)
    (re.compile(r'(Bearer\s+)([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)', re.IGNORECASE), r'\1[REDACTED_TOKEN]'),
    (re.compile(r'(token[=:\s]+)([A-Za-z0-9_-]{20,})', re.IGNORECASE), r'\1[REDACTED]'),
    
    # Email addresses (partial redaction to keep domain for debugging)
    (re.compile(r'([a-zA-Z0-9._%+-]+)@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})'), r'[REDACTED_EMAIL]@\2'),
    
    # Passwords
    (re.compile(r'(password[=:\s]+)([^\s,}\]]+)', re.IGNORECASE), r'\1[REDACTED]'),
    
    # Account numbers (generic patterns for financial account IDs)
    (re.compile(r'(account_id[=:\s]+)([^\s,}\]]+)', re.IGNORECASE), r'\1[REDACTED_ACCOUNT]'),
    (re.compile(r'(accountId[=:\s]+)([^\s,}\]]+)', re.IGNORECASE), r'\1[REDACTED_ACCOUNT]'),
    
    # Authorization IDs
    (re.compile(r'(authorization_id[=:\s]+)([^\s,}\]]+)', re.IGNORECASE), r'\1[REDACTED]'),
    
    # Connection strings with credentials
    (re.compile(r'(postgresql://[^:]+:)([^@]+)(@)', re.IGNORECASE), r'\1[REDACTED]\3'),
    
    # Encryption keys
    (re.compile(r'(encryption_key[=:\s]+)([^\s,}\]]+)', re.IGNORECASE), r'\1[REDACTED]'),
]

# Specific field names that indicate the entire value should be redacted
SENSITIVE_FIELD_NAMES = {
    'user_id', 'userId', 'account_id', 'accountId', 'snaptrade_user_id',
    'snaptrade_token', 'user_secret', 'userSecret', 'api_key', 'apikey',
    'secret', 'token', 'password', 'authorization_id', 'client_id',
    'consumer_key', 'encryption_key', 'jwt', 'access_token', 'refresh_token'
}


def sanitize_message(message: str) -> str:
    """
    Sanitize a log message by redacting sensitive information.
    
    Args:
        message: The log message to sanitize
        
    Returns:
        Sanitized message with sensitive data redacted
    """
    if not message:
        return message
    
    sanitized = message
    
    # Apply all regex patterns
    for pattern, replacement in SENSITIVE_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    
    return sanitized


def sanitize_dict(data: Dict[str, Any], depth: int = 0) -> Dict[str, Any]:
    """
    Recursively sanitize a dictionary by redacting sensitive field values.
    
    Args:
        data: Dictionary to sanitize
        depth: Current recursion depth (to prevent infinite loops)
        
    Returns:
        Sanitized dictionary copy
    """
    if depth > 10:  # Prevent infinite recursion
        return {"[TRUNCATED]": "max depth exceeded"}
    
    sanitized = {}
    for key, value in data.items():
        key_lower = key.lower()
        
        # Check if this is a sensitive field
        if key_lower in {k.lower() for k in SENSITIVE_FIELD_NAMES}:
            sanitized[key] = '[REDACTED]'
        elif isinstance(value, dict):
            sanitized[key] = sanitize_dict(value, depth + 1)
        elif isinstance(value, list):
            sanitized[key] = [
                sanitize_dict(item, depth + 1) if isinstance(item, dict) 
                else sanitize_message(str(item)) if isinstance(item, str)
                else item
                for item in value
            ]
        elif isinstance(value, str):
            sanitized[key] = sanitize_message(value)
        else:
            sanitized[key] = value
    
    return sanitized


class SensitiveDataFilter(logging.Filter):
    """
    Logging filter that sanitizes sensitive data from log messages.
    
    This filter redacts:
    - User IDs, Account IDs, Connection IDs (UUIDs)
    - SnapTrade user IDs and secrets
    - API keys and tokens
    - Email addresses (partial)
    - Passwords
    - Database connection strings with credentials
    """
    
    def filter(self, record: logging.LogRecord) -> bool:
        """
        Filter and sanitize the log record.
        
        Args:
            record: The log record to filter
            
        Returns:
            True (always allows the record, but sanitized)
        """
        # Sanitize the main message
        if record.msg:
            if isinstance(record.msg, str):
                record.msg = sanitize_message(record.msg)
            elif isinstance(record.msg, dict):
                record.msg = sanitize_dict(record.msg)
        
        # Sanitize arguments if present
        if record.args:
            if isinstance(record.args, dict):
                record.args = sanitize_dict(record.args)
            elif isinstance(record.args, tuple):
                sanitized_args = []
                for arg in record.args:
                    if isinstance(arg, str):
                        sanitized_args.append(sanitize_message(arg))
                    elif isinstance(arg, dict):
                        sanitized_args.append(sanitize_dict(arg))
                    else:
                        # Preserve non-string types to avoid breaking %-formatting (e.g., %d expects int)
                        sanitized_args.append(arg)
                record.args = tuple(sanitized_args)
        
        return True


class SafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that won't crash logging on Windows file-lock rollover issues."""

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except PermissionError:
            # Windows can fail to rename log files if *any* process has the file open
            # (common with uvicorn --reload spawning multiple processes).
            # Skip rollover to avoid breaking application logging.
            return


def setup_logging():
    """Configure logging with file and console handlers, including sensitive data filtering"""
    
    # Create the sensitive data filter
    sensitive_filter = SensitiveDataFilter()
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Avoid duplicate handlers when running under reload/interactive sessions.
    for existing in list(root_logger.handlers):
        try:
            existing.close()
        except Exception:
            pass
        root_logger.removeHandler(existing)
    
    # Add filter to root logger (applies to all handlers)
    root_logger.addFilter(sensitive_filter)
    
    # Console handler (INFO level)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.addFilter(sensitive_filter)
    console_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)
    
    # File handler (DEBUG level)
    is_windows = os.name == "nt"
    pid_suffix = f".{os.getpid()}" if is_windows else ""

    file_handler = SafeRotatingFileHandler(
        LOGS_DIR / f"app{pid_suffix}.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.addFilter(sensitive_filter)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    )
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)
    
    # Error file handler
    error_handler = SafeRotatingFileHandler(
        LOGS_DIR / f"error{pid_suffix}.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
        delay=True,
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.addFilter(sensitive_filter)
    error_handler.setFormatter(file_formatter)
    root_logger.addHandler(error_handler)
    
    # Job-specific logger
    jobs_logger = logging.getLogger("jobs")
    jobs_logger.addFilter(sensitive_filter)
    jobs_handler = SafeRotatingFileHandler(
        LOGS_DIR / f"jobs{pid_suffix}.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
        delay=True,
    )
    jobs_handler.addFilter(sensitive_filter)
    jobs_handler.setFormatter(file_formatter)
    jobs_logger.addHandler(jobs_handler)
    jobs_logger.setLevel(logging.INFO)
    jobs_logger.propagate = True
    
    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Get logger instance for module with sensitive data filtering"""
    logger = logging.getLogger(name)
    # Ensure the filter is applied
    if not any(isinstance(f, SensitiveDataFilter) for f in logger.filters):
        logger.addFilter(SensitiveDataFilter())
    return logger


# Utility functions for safe logging
def safe_log_id(identifier: Optional[str], prefix: str = "ID") -> str:
    """
    Create a safe representation of an ID for logging.
    Shows only the last 4 characters for debugging.
    
    Args:
        identifier: The ID to safely represent
        prefix: A prefix to identify the type of ID
        
    Returns:
        Safe string like "ID:...abcd" or "ID:None"
    """
    if not identifier:
        return f"{prefix}:None"
    if len(identifier) <= 4:
        return f"{prefix}:[REDACTED]"
    return f"{prefix}:...{identifier[-4:]}"


def safe_log_email(email: Optional[str]) -> str:
    """
    Create a safe representation of an email for logging.
    Shows only the domain for debugging.
    
    Args:
        email: The email to safely represent
        
    Returns:
        Safe string like "user@domain.com" -> "[REDACTED]@domain.com"
    """
    if not email or '@' not in email:
        return "[REDACTED_EMAIL]"
    _, domain = email.rsplit('@', 1)
    return f"[REDACTED]@{domain}"
