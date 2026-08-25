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

## Features (Session: June 2026)
- **Client Email Notifications** — via Emergent-managed email proxy. Auto-sent when: (1) engagement created with a client email, (2) a milestone is cleared and the next checkpoint becomes ready. Dark-branded HTML templates with portal CTA button. Fire-and-forget (`asyncio.create_task`), guarded by the `_assert_safe_email` gate.
- **Change Request Thread** — a change request opens a per-milestone thread (`change_thread[]`, `change_status: open|resolved`). Client posts from the portal (public endpoint), agency replies from its view (authed endpoint) and can Mark Resolved.
- **Stripe Payments** — cleared milestones show a real "Pay" button in the client portal. Backend creates a Stripe hosted checkout session (amount = fee + expense, server-side) via `emergentintegrations` StripeCheckout. Payment confirmed by polling `GET /api/public/payments/{session_id}/status` (+ webhook `/api/webhook/stripe`); milestone `payment_status` → `paid` idempotently via `payment_transactions` collection.

## Backend Endpoints (FastAPI, prefix `/api`)
- `POST /auth/session` — exchange Emergent OAuth session_id → app session_token
- `GET  /auth/me` — return signed-in user
- `GET  /engagements` — list engagements for the signed-in agency
- `POST /engagements` — create engagement `{client_name, client_email?, milestones[]}`
- `GET  /public/engagements/{token}` — client-facing engagement read
- `POST /public/engagements/{token}/accept` — client accepts scope, timestamped audit
- `POST /public/engagements/{token}/milestones/{ms_id}/clear` — client clears next awaiting milestone with `{client_name?, client_email?}`; enforces sequential order; sets `cleared_by_name`, `cleared_by_email`, `cleared_at`, `payment_status="requested"`
- `POST /public/engagements/{token}/milestones/{ms_id}/request-change` — opens change thread (`{note, author_name?}`)
- `POST /public/engagements/{token}/milestones/{ms_id}/change-messages` — client thread reply `{body, author_name?}`
- `POST /engagements/{eng_id}/milestones/{ms_id}/change-messages` — agency thread reply (authed)
- `POST /engagements/{eng_id}/milestones/{ms_id}/resolve-change` — agency marks thread resolved (authed)
- `POST /public/engagements/{token}/milestones/{ms_id}/pay` — create Stripe checkout, returns `{url, session_id}`
- `GET  /public/payments/{session_id}/status` — poll payment; marks paid when Stripe confirms
- `POST /webhook/stripe` — Stripe webhook backup path

## Data Model
`engagements`: `{engagement_id, agency_user_id, client_name, client_email?, share_token, status, scope_accepted_at?, created_at, milestones[]}`
`milestones[]`: `{milestone_id, title, fee, expense, status, payment_status(not_requested|requested|paid), change_request?, change_status?(open|resolved), change_thread[]: {message_id, author(client|agency), author_name?, body, created_at}, payment_session_id?, paid_at?, cleared_by_name?, cleared_by_email?, cleared_at?}`
`payment_transactions`: `{session_id, engagement_id, milestone_id, share_token, amount, currency, payment_status(initiated|paid), created_at, paid_at?}`
`user_sessions`: TTL by `expires_at`

## Integrations
- **Emergent-managed Google Sign-In** — no keys required.
- **Emergent-managed Email (Resend proxy)** — `EMERGENT_EMAIL_KEY` + `EMAIL_FROM_NAME=Checkpoint` in backend/.env. Sender address fixed by platform.
- **Stripe (test)** — `STRIPE_API_KEY=sk_test_emergent` in backend/.env via `emergentintegrations` StripeCheckout. `APP_BASE_URL` in backend/.env is the preview origin used for checkout redirect + email links (must be updated on fork).
