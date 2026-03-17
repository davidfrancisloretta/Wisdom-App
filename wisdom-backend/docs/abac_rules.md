# ABAC (Attribute-Based Access Control) Rules — WISDOM APP

## Overview

ABAC provides **instance-level** access control after RBAC has already confirmed the user's role has the correct **type-level** permission. ABAC answers: "Can this specific user access this specific record?"

## Bypass Roles

The following roles bypass all ABAC checks:

- `super_admin`
- `admin`
- `chief_therapist`

These roles have unrestricted access to all records within their RBAC-permitted resource types.

## Rule 1: Case Access (`check_case_access`)

**Purpose:** Controls which users can view/modify a specific child case.

**Logic:**
```
IF user.role IN (super_admin, admin, chief_therapist):
    ALLOW  (bypass)
ELSE IF EXISTS active CaseAssignment WHERE case_id = target AND user_id = current:
    ALLOW
ELSE:
    DENY
```

**Affected roles:**
- `supervisor` — must have a supervisor CaseAssignment for the case
- `therapist` — must have a primary_therapist CaseAssignment for the case
- `nurturer` — must have a nurturer CaseAssignment for the case

**Data source:** `case_assignments` table (`is_active = true`)

## Rule 2: Note Access (`check_note_access`)

**Purpose:** Controls which users can view/modify a specific case note.

**Logic:**
```
IF user.role IN (super_admin, admin, chief_therapist):
    ALLOW  (bypass)
ELSE IF note.author_id = current_user.id:
    ALLOW  (author can always access their own notes)
ELSE IF EXISTS active CaseAssignment WHERE case_id = note.case_id
         AND user_id = current AND assignment_type = 'supervisor':
    ALLOW  (supervisor of the case can access all notes)
ELSE:
    DENY
```

**Affected roles:**
- `therapist` — can only access notes they authored
- `supervisor` — can access all notes in cases they supervise
- `nurturer` — can only access notes they authored

**Data sources:** `case_notes` table, `case_assignments` table

## Rule 3: Supervisor Scope (`check_supervisor_scope`)

**Purpose:** Determines if a supervisor has oversight of a specific case through team membership.

**Logic:**
```
IF user.role IN (super_admin, admin, chief_therapist):
    ALLOW  (bypass)
ELSE:
    1. Get all case_ids where user has a supervisor CaseAssignment
    2. If target case_id is directly supervised: ALLOW
    3. Get all user_ids assigned to supervised cases (team members)
    4. If any team member is also assigned to target case: ALLOW
    5. Otherwise: DENY
```

**Purpose:** This rule enables team-based scoping — a supervisor can access any case that shares a staff member with a case they directly supervise.

## Rule 4: Parent Case Access (`check_parent_case_access`)

**Purpose:** Ensures parents can only access their own child's case data.

**Logic:**
```
IF EXISTS UserAttribute WHERE user_id = parent AND key = 'child_case_id'
     AND value = target_case_id:
    ALLOW
ELSE:
    DENY
```

**Data source:** `user_attributes` table (key: `child_case_id`)

**Note:** A parent may have multiple `child_case_id` attributes if they have multiple children in the system.

## Enforcement Points

| Guard Function | ABAC Rule Used | Applied In |
|---------------|----------------|------------|
| `require_case_access` | `check_case_access` | Case detail, case notes, interventions |
| `require_note_access` | `check_note_access` | Note detail, note edit |
| `get_current_user` | None (auth only) | All authenticated endpoints |
| `require_role(*roles)` | None (RBAC only) | Admin panel, analytics |

## Attribute Storage

| Attribute | Table | Key | Value |
|-----------|-------|-----|-------|
| Role assignment | `users.role_id` → `roles.name` | - | Role name |
| Case assignment | `case_assignments` | `assignment_type` | `primary_therapist`, `nurturer`, `supervisor` |
| Parent-child link | `user_attributes` | `child_case_id` | UUID of the child case |
| Team membership | Derived from shared `case_assignments` | - | Computed at query time |
| Billing staff | `user_attributes` | `is_billing_staff` | `true`/`false` |
| Comms staff | `user_attributes` | `is_comms_staff` | `true`/`false` |

## Security Considerations

1. **ABAC runs server-side only** — never trust client-side permission checks for data access
2. **Both RBAC and ABAC must pass** — ABAC is evaluated only after RBAC succeeds
3. **Bypass roles are minimal** — only 3 roles bypass ABAC checks
4. **Assignment checks use `is_active` flag** — deactivated assignments are ignored
5. **Parent access is strictly scoped** — verified through explicit attribute linkage, not inferred
