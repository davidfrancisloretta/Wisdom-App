"""RBAC middleware and utilities using the Casbin enforcer."""

from app.auth.casbin_adapter import check_rbac


def has_permission(role: str, resource: str, action: str) -> bool:
    """Check if a role has RBAC permission for the given resource and action."""
    return check_rbac(role, resource, action)
