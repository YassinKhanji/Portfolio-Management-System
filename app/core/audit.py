"""
Audit Logging Module

Logs all security-relevant and financial actions for compliance and monitoring.
Stores logs in the database Log table with structured metadata.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from functools import wraps

from fastapi import Request

logger = logging.getLogger(__name__)


# Audit action types
class AuditAction:
    """Standard audit action types for financial compliance."""
    
    # Authentication
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGIN_FAILURE = "LOGIN_FAILURE"
    LOGOUT = "LOGOUT"
    REGISTER = "REGISTER"
    PASSWORD_CHANGE = "PASSWORD_CHANGE"
    
    # Data Access
    VIEW_BALANCE = "VIEW_BALANCE"
    VIEW_HOLDINGS = "VIEW_HOLDINGS"
    VIEW_TRANSACTIONS = "VIEW_TRANSACTIONS"
    VIEW_PORTFOLIO = "VIEW_PORTFOLIO"
    EXPORT_DATA = "EXPORT_DATA"
    
    # Trading
    TRADE_INITIATED = "TRADE_INITIATED"
    TRADE_EXECUTED = "TRADE_EXECUTED"
    TRADE_FAILED = "TRADE_FAILED"
    ORDER_PLACED = "ORDER_PLACED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    
    # Account Management
    SNAPTRADE_CONNECT = "SNAPTRADE_CONNECT"
    SNAPTRADE_DISCONNECT = "SNAPTRADE_DISCONNECT"
    SETTINGS_CHANGED = "SETTINGS_CHANGED"
    RISK_PROFILE_CHANGED = "RISK_PROFILE_CHANGED"
    
    # Admin Actions
    ADMIN_VIEW_CLIENT = "ADMIN_VIEW_CLIENT"
    ADMIN_EDIT_CLIENT = "ADMIN_EDIT_CLIENT"
    ADMIN_DELETE_CLIENT = "ADMIN_DELETE_CLIENT"
    ADMIN_SYNC_HOLDINGS = "ADMIN_SYNC_HOLDINGS"
    ADMIN_EMERGENCY_STOP = "ADMIN_EMERGENCY_STOP"
    SYSTEM_CONFIG_CHANGE = "SYSTEM_CONFIG_CHANGE"


def get_client_ip(request: Optional[Request]) -> str:
    """Extract client IP from request, handling proxies."""
    if not request:
        return "unknown"
    
    # Check forwarded headers (Railway, nginx, etc.)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    
    # Direct connection
    if request.client:
        return request.client.host
    
    return "unknown"


def audit_log(
    action: str,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    request: Optional[Request] = None,
    resource: Optional[str] = None,
    resource_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    success: bool = True,
    admin_action: bool = False,
    db_session=None,
):
    """
    Log an audit event to the database.
    
    Args:
        action: The action type (use AuditAction constants)
        user_id: ID of the user performing the action
        user_email: Email of the user (for easier log reading)
        request: FastAPI request object (for IP extraction)
        resource: Type of resource accessed (e.g., "balance", "holdings")
        resource_id: Specific resource ID if applicable
        details: Additional details about the action
        success: Whether the action succeeded
        admin_action: Whether this is an admin action
        db_session: Database session to use
    """
    try:
        # Build metadata
        metadata = {
            "action": action,
            "success": success,
            "ip_address": get_client_ip(request),
            "user_agent": request.headers.get("User-Agent", "unknown") if request else "unknown",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        
        if user_email:
            metadata["user_email"] = user_email
        if resource:
            metadata["resource"] = resource
        if resource_id:
            metadata["resource_id"] = resource_id
        if details:
            metadata["details"] = details
        
        # Build log message
        parts = [f"AUDIT: {action}"]
        if user_email:
            parts.append(f"user={user_email}")
        if resource:
            parts.append(f"resource={resource}")
        if not success:
            parts.append("FAILED")
        
        message = " | ".join(parts)
        
        # Log level based on action type
        if not success or action in [AuditAction.LOGIN_FAILURE, AuditAction.TRADE_FAILED]:
            level = "warning"
        elif admin_action or action.startswith("ADMIN_"):
            level = "info"
        else:
            level = "info"
        
        # Write to database if session provided
        if db_session:
            try:
                from app.models.database import Log
                
                log_entry = Log(
                    id=str(uuid.uuid4()),
                    timestamp=datetime.now(timezone.utc),
                    level=level,
                    message=message,
                    component="audit",
                    user_id=user_id,
                    admin_action=admin_action,
                    metadata_json=metadata,
                )
                db_session.add(log_entry)
                db_session.commit()
            except Exception as db_err:
                logger.error(f"Failed to write audit log to database: {db_err}")
        
        # Also log to standard logger
        log_func = getattr(logger, level, logger.info)
        log_func(message, extra={"audit_metadata": metadata})
        
    except Exception as e:
        logger.error(f"Audit logging failed: {e}")


def audit_login(
    email: str,
    success: bool,
    request: Optional[Request] = None,
    user_id: Optional[str] = None,
    failure_reason: Optional[str] = None,
    db_session=None,
):
    """Log a login attempt."""
    audit_log(
        action=AuditAction.LOGIN_SUCCESS if success else AuditAction.LOGIN_FAILURE,
        user_id=user_id,
        user_email=email,
        request=request,
        success=success,
        details={"failure_reason": failure_reason} if failure_reason else None,
        db_session=db_session,
    )


def audit_data_access(
    action: str,
    user_id: str,
    user_email: str,
    resource: str,
    request: Optional[Request] = None,
    resource_id: Optional[str] = None,
    db_session=None,
):
    """Log data access (viewing balance, holdings, etc.)."""
    audit_log(
        action=action,
        user_id=user_id,
        user_email=user_email,
        request=request,
        resource=resource,
        resource_id=resource_id,
        db_session=db_session,
    )


def audit_trade(
    action: str,
    user_id: str,
    user_email: str,
    request: Optional[Request] = None,
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    quantity: Optional[float] = None,
    price: Optional[float] = None,
    order_id: Optional[str] = None,
    success: bool = True,
    error: Optional[str] = None,
    db_session=None,
):
    """Log a trade/order action."""
    details = {}
    if symbol:
        details["symbol"] = symbol
    if side:
        details["side"] = side
    if quantity:
        details["quantity"] = quantity
    if price:
        details["price"] = price
    if order_id:
        details["order_id"] = order_id
    if error:
        details["error"] = error
    
    audit_log(
        action=action,
        user_id=user_id,
        user_email=user_email,
        request=request,
        resource="trade",
        resource_id=order_id,
        details=details if details else None,
        success=success,
        db_session=db_session,
    )


def audit_admin_action(
    action: str,
    admin_id: str,
    admin_email: str,
    request: Optional[Request] = None,
    target_user_id: Optional[str] = None,
    target_user_email: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    db_session=None,
):
    """Log an admin action."""
    full_details = details or {}
    if target_user_id:
        full_details["target_user_id"] = target_user_id
    if target_user_email:
        full_details["target_user_email"] = target_user_email
    
    audit_log(
        action=action,
        user_id=admin_id,
        user_email=admin_email,
        request=request,
        resource="admin",
        details=full_details if full_details else None,
        admin_action=True,
        db_session=db_session,
    )
