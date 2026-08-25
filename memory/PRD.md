# Checkpoint — Product Requirements

## Purpose
Checkpoint helps freelancers and small creative/service agencies manage client approvals and milestone-based payments across multiple engagements. Clients accept scope upfront, then clear each milestone in sequence — every clearance triggers a per-milestone payment request.

## Roles
- **Agency** — signs in with Google (Emergent-managed), owns and creates engagements.
- **Client** — accesses their engagement via a unique public share link (no sign-up).

## Key Screens
1. **Welcome** — Continue with Google · Explore sample workspace
2. **Agency Dashboard** — lists all engagements the signed-in user created (client name, X of Y cleared, ACTIVE / AWAITING SCOPE pill, payment pulse) + `+ New engagement`
3. **New Engagement Flow** — client name/email → add milestones one by one (description, fee, optional expense) → generate share link
4. **Share Link** — copy-to-clipboard box with the client URL
5. **Agency Engagement View** — trajectory, share link, "Cleared by <name>, <date>" per cleared milestone, payment placeholder
6. **Client Portal** (`/{share_token}`) — trajectory only; can accept scope (timestamped), clear next awaiting milestone (captures name/email), or request change
7. **Payment Placeholder** — per-milestone request labeled "Placeholder link — connects to a real payment gateway in production"

## Design
- Mission-control aesthetic: deep charcoal `#17151D` framing, panels `#221E2B`, purple accent `#9B6CFF`, cleared green `#55C49A`.
- Client-facing cards use light surface `#F6F4F8` for high-contrast legibility.
- Language: "Cleared" and "Awaiting Clearance" used sparingly in status indicators only.

## Backend Endpoints (FastAPI, prefix `/api`)
- `POST /auth/session` — exchange Emergent OAuth session_id → app session_token
- `GET  /auth/me` — return signed-in user
- `GET  /engagements` — list engagements for the signed-in agency
- `POST /engagements` — create engagement `{client_name, client_email?, milestones[]}`
- `GET  /public/engagements/{token}` — client-facing engagement read
- `POST /public/engagements/{token}/accept` — client accepts scope, timestamped audit
- `POST /public/engagements/{token}/milestones/{ms_id}/clear` — client clears next awaiting milestone with `{client_name?, client_email?}`; enforces sequential order; sets `cleared_by_name`, `cleared_by_email`, `cleared_at`, `payment_status="requested"`
- `POST /public/engagements/{token}/milestones/{ms_id}/request-change` — attach change note

## Data Model
`engagements`: `{engagement_id, agency_user_id, client_name, client_email?, share_token, status, scope_accepted_at?, created_at, milestones[]}`
`milestones[]`: `{milestone_id, title, fee, expense, status, payment_status, change_request?, cleared_by_name?, cleared_by_email?, cleared_at?}`
`user_sessions`: TTL by `expires_at`

## Integrations
- **Emergent-managed Google Sign-In** — no keys required.
- **Payments** — placeholder text today; real gateway wiring pending user's key delivery.
