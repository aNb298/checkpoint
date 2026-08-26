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
- **Payment Reminders** — hourly background sweep (`payment_reminder_loop`) emails the client a friendly nudge when a payment request sits unpaid for `REMINDER_DAYS` (default 3, env-tunable) days; re-nudges every 3 days via `payment_reminder_at` stamp on the milestone.
- **Earnings Overview** — dashboard totals panel (testID `earnings-panel`): Cleared / Awaiting / Paid amounts computed client-side across all engagements.
- **Deliverable Attachments** — agency attaches preview links or uploads files (≤15 MB, Emergent Object Storage) per milestone; clients open them from the portal via `GET /api/public/engagements/{token}/attachments/{att_id}` (files streamed through backend, links 307-redirect). `storage_path` kept in DB only, never in API responses.
- **Thread Alerts** — agency user is emailed when a client opens a change request or replies on a thread.
- **Milestone Editing** — pre-scope only (status `awaiting_scope_acceptance`): agency can edit title/fee/expense, reorder (move up/down), delete (min 1 kept), and add checkpoints from the engagement view. All blocked with 409 once scope accepted or archived.
- **Client Receipts** — on payment confirmation (`_mark_paid`), client is emailed a receipt (amount, checkpoint, paid-to-date vs total).
- **Engagement Archive** — agency can archive/restore engagements (`status: archived`, restore returns to active/awaiting based on `scope_accepted_at`). Dashboard hides archived by default with a "Show archived" toggle; earnings totals computed over visible list. Client accept blocked on archived (409); portal shows read-only archived notice.
- **PDF Summary** — `GET /api/public/engagements/{token}/summary.pdf` (reportlab): milestones table with fees, statuses, clearances, payments + totals. "PDF summary" button in agency engagement view.

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
- `POST /engagements/{eng_id}/milestones/{ms_id}/attachments` — agency adds link `{name, url}` (authed)
- `POST /engagements/{eng_id}/milestones/{ms_id}/attachments/upload` — agency uploads file (multipart, authed)
- `DELETE /engagements/{eng_id}/milestones/{ms_id}/attachments/{att_id}` — agency removes attachment (authed)
- `GET  /public/engagements/{token}/attachments/{att_id}` — client opens file (streamed) or link (redirect)
- `PUT  /engagements/{eng_id}/milestones/{ms_id}` — edit title/fee/expense (authed, pre-scope only)
- `POST /engagements/{eng_id}/milestones` — add checkpoint (authed, pre-scope only)
- `DELETE /engagements/{eng_id}/milestones/{ms_id}` — remove checkpoint (authed, pre-scope, min 1)
- `POST /engagements/{eng_id}/milestones/{ms_id}/move` — `{direction: up|down}` (authed, pre-scope)
- `POST /engagements/{eng_id}/archive` / `POST /engagements/{eng_id}/unarchive` — authed
- `GET  /public/engagements/{token}/summary.pdf` — PDF engagement summary

## Data Model
`engagements`: `{engagement_id, agency_user_id, client_name, client_email?, share_token, status, scope_accepted_at?, created_at, milestones[]}`
`milestones[]`: `{milestone_id, title, fee, expense, status, payment_status(not_requested|requested|paid), change_request?, change_status?(open|resolved), change_thread[]: {message_id, author(client|agency), author_name?, body, created_at}, attachments[]: {attachment_id, kind(file|link), name, url?, content_type?, storage_path?(DB-only), created_at}, payment_session_id?, paid_at?, payment_reminder_at?, cleared_by_name?, cleared_by_email?, cleared_at?}`
`payment_transactions`: `{session_id, engagement_id, milestone_id, share_token, amount, currency, payment_status(initiated|paid), created_at, paid_at?}`
`user_sessions`: TTL by `expires_at`

## Integrations
- **Emergent-managed Google Sign-In** — no keys required.
- **Emergent-managed Email (Resend proxy)** — `EMERGENT_EMAIL_KEY` + `EMAIL_FROM_NAME=Checkpoint` in backend/.env. Sender address fixed by platform.
- **Stripe (test)** — `STRIPE_API_KEY=sk_test_emergent` in backend/.env via `emergentintegrations` StripeCheckout. `APP_BASE_URL` in backend/.env is the preview origin used for checkout redirect + email links (must be updated on fork).
- **Emergent Object Storage** — `EMERGENT_LLM_KEY` in backend/.env; init at startup; files at `checkpoint/uploads/{user_id}/{uuid}.{ext}`.
