"""PostgreSQL-backed Casbin adapter and policy seeding."""

import os
from pathlib import Path

import casbin
from sqlalchemy import Column, Integer, String, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base


# ---------------------------------------------------------------------------
# Casbin rule table
# ---------------------------------------------------------------------------

class CasbinRule(Base):
    __tablename__ = "casbin_rule"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ptype = Column(String(255), nullable=False, default="p")
    v0 = Column(String(255), default="")
    v1 = Column(String(255), default="")
    v2 = Column(String(255), default="")
    v3 = Column(String(255), default="")
    v4 = Column(String(255), default="")
    v5 = Column(String(255), default="")


# ---------------------------------------------------------------------------
# Policy definitions
# ---------------------------------------------------------------------------

SEED_POLICIES: list[tuple[str, str, str, str, str]] = [
    # Super Admin — full access
    ("p", "super_admin", "/*", "*", "allow"),

    # Admin — all clinical + operational, no system config
    ("p", "admin", "/cases/*", "*", "allow"),
    ("p", "admin", "/assessments/*", "*", "allow"),
    ("p", "admin", "/scheduling/*", "*", "allow"),
    ("p", "admin", "/messaging/*", "*", "allow"),
    ("p", "admin", "/payments/*", "*", "allow"),
    ("p", "admin", "/donations/*", "*", "allow"),
    ("p", "admin", "/analytics/*", "*", "allow"),
    ("p", "admin", "/admin/users/*", "*", "allow"),
    ("p", "admin", "/admin/roles/*", "*", "allow"),
    ("p", "admin", "/admin/audit-log/*", "GET", "allow"),
    ("p", "admin", "/admin/consent/*", "*", "allow"),
    ("p", "admin", "/admin/assessments/*", "*", "allow"),
    ("p", "admin", "/admin/config/*", "*", "deny"),

    # Chief Therapist — all clinical
    ("p", "chief_therapist", "/cases/*", "*", "allow"),
    ("p", "chief_therapist", "/assessments/*", "*", "allow"),
    ("p", "chief_therapist", "/scheduling/*", "GET", "allow"),
    ("p", "chief_therapist", "/analytics/*", "GET", "allow"),

    # Supervisor — team-scoped (ABAC enforces team scope)
    ("p", "supervisor", "/cases/*", "GET", "allow"),
    ("p", "supervisor", "/cases/*/notes", "*", "allow"),
    ("p", "supervisor", "/assessments/*/results", "GET", "allow"),
    ("p", "supervisor", "/scheduling/*", "GET", "allow"),

    # Therapist — assigned cases only (ABAC enforces assignment)
    ("p", "therapist", "/cases/*", "GET", "allow"),
    ("p", "therapist", "/cases/*/notes", "*", "allow"),
    ("p", "therapist", "/cases/*/interventions", "*", "allow"),
    ("p", "therapist", "/assessments/*/results", "GET", "allow"),
    ("p", "therapist", "/scheduling/*", "GET", "allow"),

    # Nurturer — view + observations on assigned cases
    ("p", "nurturer", "/cases/*", "GET", "allow"),
    ("p", "nurturer", "/cases/*/notes", "POST", "allow"),
    ("p", "nurturer", "/cases/*/milestones", "POST", "allow"),

    # Staff — operational only, no clinical
    ("p", "staff", "/scheduling/*", "*", "allow"),
    ("p", "staff", "/payments/*", "*", "allow"),
    ("p", "staff", "/donations/*", "*", "allow"),
    ("p", "staff", "/messaging/campaigns", "*", "allow"),

    # Parent — own child's data only
    ("p", "parent", "/parent/cases/*", "GET", "allow"),
    ("p", "parent", "/parent/assessments/*", "*", "allow"),
    ("p", "parent", "/parent/portal/*", "GET", "allow"),
]


# ---------------------------------------------------------------------------
# Enforcer factory
# ---------------------------------------------------------------------------

_enforcer: casbin.Enforcer | None = None


def reset_enforcer() -> None:
    """Reset the cached enforcer (used by tests when model changes)."""
    global _enforcer
    _enforcer = None


def get_enforcer() -> casbin.Enforcer:
    """Get or create the Casbin enforcer using the model config file."""
    global _enforcer
    if _enforcer is not None:
        return _enforcer

    model_path = str(Path(__file__).parent / "casbin_model.conf")
    _enforcer = casbin.Enforcer(model_path)
    return _enforcer


def load_policies_into_enforcer(enforcer: casbin.Enforcer) -> None:
    """Load all policies from SEED_POLICIES into the in-memory enforcer."""
    enforcer.clear_policy()
    for ptype, v0, v1, v2, v3 in SEED_POLICIES:
        if ptype == "p":
            enforcer.add_policy(v0, v1, v2, v3)
        elif ptype == "g":
            enforcer.add_grouping_policy(v0, v1)


def check_rbac(role: str, resource: str, action: str) -> bool:
    """Check if a role has permission to perform an action on a resource."""
    enforcer = get_enforcer()
    if not enforcer.get_policy():
        load_policies_into_enforcer(enforcer)
    return enforcer.enforce(role, resource, action)


# ---------------------------------------------------------------------------
# DB seeding
# ---------------------------------------------------------------------------

async def seed_casbin_policies(db: AsyncSession) -> int:
    """Seed casbin_rule table if empty. Returns number of rules inserted."""
    result = await db.execute(text("SELECT COUNT(*) FROM casbin_rule"))
    count = result.scalar()
    if count and count > 0:
        return 0

    inserted = 0
    for ptype, v0, v1, v2, v3 in SEED_POLICIES:
        rule = CasbinRule(ptype=ptype, v0=v0, v1=v1, v2=v2, v3=v3)
        db.add(rule)
        inserted += 1

    await db.commit()
    return inserted


async def load_policies_from_db(db: AsyncSession) -> None:
    """Load policies from the casbin_rule DB table into the enforcer."""
    from sqlalchemy import select as sa_select

    enforcer = get_enforcer()
    enforcer.clear_policy()

    result = await db.execute(sa_select(CasbinRule))
    rules = result.scalars().all()

    for rule in rules:
        if rule.ptype == "p":
            enforcer.add_policy(rule.v0, rule.v1, rule.v2, rule.v3)
        elif rule.ptype == "g":
            enforcer.add_grouping_policy(rule.v0, rule.v1)
