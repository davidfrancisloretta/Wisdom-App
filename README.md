# WISDOM App

**Wellness & Integrated Support for Development, Observation & Mentoring**

A comprehensive clinical case management and therapeutic support platform built for **The Ark Centre** — a child trauma recovery organization. WISDOM manages the full lifecycle of child cases with integrated assessments, scheduling, AI-powered insights, secure communications, and donation management.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Frontend** | Next.js 16, React 19, TypeScript 5, Tailwind CSS v4, shadcn/ui, Recharts, Framer Motion |
| **Backend** | FastAPI (Python 3.11), SQLAlchemy 2.0 (async), Alembic |
| **Database** | PostgreSQL 16 (asyncpg) |
| **Cache/Queue** | Redis 7 |
| **Auth** | NextAuth.js + JWT (python-jose), Argon2 hashing, Casbin RBAC/ABAC |
| **AI/LLM** | LiteLLM (GPT-4o with graceful fallback) |
| **Payments** | Razorpay (INR) + Stripe (USD) |
| **Messaging** | WhatsApp Business API |
| **Security** | AES-256-GCM field-level encryption, Sentry error tracking |
| **Infrastructure** | Docker Compose, PWA (next-pwa) |

---

## Features

### Core Platform (Phases 1–3)
- **Case Management** — Create and manage child cases with encrypted PII (names, DOB, guardian info)
- **Staff Assignments** — Primary therapist, supervisor, nurturer roles per case
- **Case Notes** — Encrypted session notes, observations, and intervention records
- **Intervention Plans & Milestones** — Goal tracking with progress markers
- **Assessment Library** — DSM-5 templates with PDF ingestion and AI-powered question extraction
- **Scoring Engine** — Automatic domain score calculation with threshold detection
- **Risk Alerts** — P0/P1/P2 severity levels with WhatsApp notification triggers
- **Role-Based Access Control** — 9 roles (super_admin → public) with attribute-based policies
- **Consent Tracking** — Guardian consent records for data access
- **Audit Logging** — Append-only activity log for all state changes

### Scheduling, WhatsApp & Payments (Phase 4)
- **Room Scheduling** — Bookings with conflict detection, recurring RRULE expansion (90-day lookahead), equipment tracking, and maintenance windows
- **WhatsApp Integration** — 7 template types with 3-retry exponential backoff and dead letter queue fallback
- **Scheduled Notifications** — Background processor on 5-minute intervals
- **Invoicing** — PDF generation with GST calculations via ReportLab
- **Payment Processing** — Razorpay Orders/Payment Links + Stripe PaymentIntents/Subscriptions with webhook handlers
- **Donation Portal** — Campaign management, one-time/recurring donations, receipt PDF generation, INR/USD toggle

### Public Portal, Analytics & AI (Phase 5)
- **Public Website** — Home page with impact stats, searchable articles, resource directory, crisis support, workshop listings, and counselor profiles
- **Analytics Dashboard** — Case trends, assessment completion, room utilization, staff workload, donation metrics — all Redis-cached with date-range filters
- **AI Risk Detector** — Analyzes case notes + assessment trends for behavioral risk patterns
- **AI Clinical Summary** — Synthesizes case notes into structured clinical summaries
- **AI Advice Generator** — Parent guidance derived from assessment results
- **AI Assessment Interpreter** — Plain-language DSM-5 domain score explanations
- **AI Intervention Suggester** — Tailored intervention recommendations per case
- **WhatsApp Campaigns** — Batch broadcast (50 msgs/batch) to parent and donor groups
- **PWA + CDN** — Offline support, installable app, cache-control headers for static assets

---

## Project Structure

```
wisdom-app/
├── wisdom-backend/
│   ├── app/
│   │   ├── admin/          # System config, user management, audit
│   │   ├── ai/             # LiteLLM-powered risk detection, summaries, advice
│   │   ├── analytics/      # Dashboard aggregations with Redis caching
│   │   ├── assessments/    # DSM-5 templates, scoring, PDF parsing
│   │   ├── auth/           # JWT, RBAC/ABAC, consent, password management
│   │   ├── cases/          # Child case CRUD, notes, interventions
│   │   ├── donations/      # Campaign and donation management
│   │   ├── messaging/      # WhatsApp, notifications, campaigns
│   │   ├── payments/       # Invoicing, Razorpay, Stripe
│   │   ├── public/         # Articles, workshops, counselor profiles
│   │   ├── scheduling/     # Room bookings, maintenance windows
│   │   ├── security/       # AES-256-GCM encryption
│   │   ├── config.py       # Pydantic Settings
│   │   ├── database.py     # SQLAlchemy async engine
│   │   ├── main.py         # FastAPI entry point
│   │   └── redis_client.py # Redis connection
│   ├── alembic/            # Database migrations (5 versions)
│   ├── seeds/              # Sample data
│   ├── tests/              # 285+ passing tests
│   ├── requirements.txt
│   └── Dockerfile
├── wisdom-frontend/
│   ├── app/
│   │   ├── (auth)/         # Login pages (staff + parent)
│   │   ├── (public)/       # Public portal (articles, resources, workshops, donate)
│   │   ├── (staff)/        # Staff dashboard, cases, assessments, scheduling, analytics
│   │   ├── (parent)/       # Parent portal and assessment completion
│   │   └── admin/          # Admin dashboard, users, roles, audit log, config
│   ├── components/         # Reusable UI components (shadcn/ui)
│   ├── lib/                # API client, auth config, RBAC utilities
│   ├── middleware.ts        # Route protection
│   └── package.json
└── docker-compose.yml      # PostgreSQL 16 + Redis 7 + FastAPI
```

---

## Database Schema

**39 tables** organized across domains:

| Domain | Tables |
|--------|--------|
| **Auth** | users, roles, permissions, role_permissions, user_attributes, refresh_tokens |
| **Cases** | child_cases, case_assignments, case_notes, intervention_plans, progress_milestones, audit_logs |
| **Assessments** | assessments, assessment_sections, assessment_domains, assessment_questions, answer_options, assessment_assignments, assessment_responses, question_responses, domain_scores, risk_alerts |
| **Scheduling** | rooms, room_bookings, maintenance_windows |
| **Payments** | invoices, payments, donation_campaigns, donations |
| **Messaging** | whatsapp_messages, notifications, scheduled_notifications, dead_letter_queue |
| **Public** | public_content, workshops, workshop_registrations, counselor_profiles |
| **Admin** | consent_records, system_config |

---

## Roles & Access Control

| Role | Access Level |
|------|-------------|
| super_admin | Full system access |
| admin | User/role management, audit oversight |
| chief_therapist | Case oversight, staff management |
| supervisor | Team supervision |
| therapist | Case management, assessment assignment |
| nurturer | Support staff access |
| staff | General staff access |
| parent | Child case observation, assessment completion |
| public | Unauthenticated public portal access |

Access is enforced via **Casbin RBAC + ABAC** — case-level access is gated by staff assignment relationship and consent records.

---

## Getting Started

### Prerequisites
- Docker & Docker Compose
- Node.js 18+
- Python 3.11+

### Quick Start (Docker)

```bash
# Start all services (PostgreSQL, Redis, FastAPI)
docker-compose up

# Run database migrations
cd wisdom-backend
alembic upgrade head

# Start the frontend
cd wisdom-frontend
npm install
npm run dev
```

### Manual Setup

```bash
# Backend
cd wisdom-backend
python -m venv .venv
.venv/Scripts/activate        # Windows
source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
cp .env.example .env          # Configure environment variables
alembic upgrade head
uvicorn app.main:app --port 8000

# Frontend
cd wisdom-frontend
npm install
npm run dev                   # http://localhost:3000
```

### Environment Variables

Copy `.env.example` and configure:

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `SECRET_KEY` | JWT signing key (base64-encoded 32 bytes) |
| `ENCRYPTION_KEY` | AES-256-GCM key for PII encryption |
| `NEXTAUTH_SECRET` | NextAuth session secret |
| `WHATSAPP_TOKEN` | WhatsApp Business API token |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Razorpay credentials |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` | Stripe credentials |
| `LITELLM_API_KEY` | LLM provider API key |
| `SENTRY_DSN` | Sentry error tracking DSN |

---

## Security

- **Field-level encryption** — Child PII (names, DOB, guardian contact, address) encrypted with AES-256-GCM at the database layer
- **Password hashing** — Argon2 via passlib
- **JWT tokens** — 30-min access / 7-day refresh, stored in httpOnly cookies
- **Audit trail** — Append-only audit_logs table for all state changes
- **Consent management** — Guardian consent records gate data access
- **Error tracking** — Sentry integration on both frontend and backend

---

## Testing

```bash
cd wisdom-backend
pytest                     # 285+ passing tests
```

Tests cover Phase 4 (136 tests) and Phase 5 (149 tests) including scheduling, payments, messaging, analytics, AI features, and public portal endpoints.

---

## License

This project is proprietary software built for The Ark Centre.
