# RBAC Permission Matrix — WISDOM APP

## Roles

| # | Role | Description |
|---|------|-------------|
| 1 | **super_admin** | Full system access including configuration and audit logs |
| 2 | **admin** | All clinical + operational access, no system configuration |
| 3 | **chief_therapist** | Full clinical access across all cases and assessments |
| 4 | **supervisor** | Team-scoped clinical access with supervision capabilities |
| 5 | **therapist** | Access to assigned cases, notes, and interventions only |
| 6 | **nurturer** | View and add observations to assigned cases |
| 7 | **staff** | Operational access: scheduling, payments, donations |
| 8 | **parent** | Access to own child's case and assessments only |

## Permission Matrix

| Resource | Action | super_admin | admin | chief_therapist | supervisor | therapist | nurturer | staff | parent |
|----------|--------|:-----------:|:-----:|:---------------:|:----------:|:---------:|:--------:|:-----:|:------:|
| `/cases/*` | GET | Y | Y | Y | Y* | Y** | Y** | - | - |
| `/cases/*` | POST | Y | Y | Y | - | - | - | - | - |
| `/cases/*` | PUT | Y | Y | Y | - | - | - | - | - |
| `/cases/*` | DELETE | Y | Y | Y | - | - | - | - | - |
| `/cases/:id/notes` | GET | Y | Y | Y | Y* | Y** | - | - | - |
| `/cases/:id/notes` | POST | Y | Y | Y | Y* | Y** | Y** | - | - |
| `/cases/:id/interventions` | * | Y | Y | Y | - | Y** | - | - | - |
| `/cases/:id/milestones` | POST | Y | Y | Y | - | - | Y** | - | - |
| `/assessments/*` | GET | Y | Y | Y | Y | Y | - | - | - |
| `/assessments/*` | POST/PUT | Y | Y | Y | - | - | - | - | - |
| `/assessments/:id/results` | GET | Y | Y | Y | Y* | Y** | - | - | - |
| `/scheduling/*` | GET | Y | Y | Y | Y | Y | - | Y | - |
| `/scheduling/*` | POST/PUT/DELETE | Y | Y | - | - | - | - | Y | - |
| `/messaging/*` | * | Y | Y | - | - | - | - | - | - |
| `/messaging/campaigns` | * | Y | Y | - | - | - | - | Y | - |
| `/payments/*` | * | Y | Y | - | - | - | - | Y | - |
| `/donations/*` | * | Y | Y | - | - | - | - | Y | - |
| `/analytics/*` | GET | Y | Y | Y | - | - | - | - | - |
| `/admin/users/*` | * | Y | Y | - | - | - | - | - | - |
| `/admin/roles/*` | * | Y | Y | - | - | - | - | - | - |
| `/admin/audit-log/*` | GET | Y | - | - | - | - | - | - | - |
| `/admin/consent/*` | * | Y | Y | - | - | - | - | - | - |
| `/admin/assessments/*` | * | Y | Y | - | - | - | - | - | - |
| `/admin/config/*` | * | Y | - | - | - | - | - | - | - |
| `/admin/sessions/*` | * | Y | Y | - | - | - | - | - | - |
| `/parent/cases/:id` | GET | - | - | - | - | - | - | - | Y*** |
| `/parent/assessments/:id` | * | - | - | - | - | - | - | - | Y*** |
| `/parent/portal/*` | GET | - | - | - | - | - | - | - | Y*** |

### Legend

- **Y** = Allowed (no instance-level check needed)
- **Y*** = Allowed with ABAC team scope check
- **Y**** = Allowed with ABAC case assignment check
- **Y***** = Allowed with ABAC parent-child linkage check
- **-** = Denied

## Safety-Critical Permissions (Locked)

The following permissions **cannot** be unchecked in the Role Editor UI:

| Permission | Reason |
|-----------|--------|
| Risk alert acknowledgement | Safeguarding: alerts must always be routable to clinical staff |
| Audit log access | Compliance: audit trail must remain accessible for oversight |
| Suicidal ideation alert routing | Child safety: SI alerts must always trigger notification chain |

## Enforcement Architecture

```
Request → CORS → JWT decode → RBAC (Casbin) → ABAC (instance check) → Handler
```

1. **RBAC** (Casbin): "Can this role perform this action on this resource type?"
2. **ABAC** (Custom): "Can this specific user access this specific record instance?"
3. Both must pass. ABAC is evaluated only after RBAC passes.
