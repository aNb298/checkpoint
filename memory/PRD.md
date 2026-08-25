# Checkpoint PRD

## Problem statement
Checkpoint helps freelancers and small creative/service agencies manage client scope acceptance, sequential approvals, and milestone-based payment requests across engagements.

## Architecture
- Expo SDK 54 React Native app with responsive web preview and native-safe UI.
- FastAPI API on `/api` with MongoDB persistence.
- Emergent-managed Google OAuth for agency access; public share-token routes for clients.

## Personas
- Agency owner: creates engagements, defines fees/expenses, and monitors shared progress.
- Client: reviews and accepts scope, clears milestones, or requests changes without signup.

## Core requirements
- Timestamped scope and schedule acceptance before work can proceed.
- Checkpoint trajectory path with sequential milestone status.
- Per-milestone fee, separate expense, clearance, and payment-request status.
- Shared client portal via unique link.

## Implemented (2026-02-14)
- Mission-control welcome, agency dashboard, client portal, and scope acceptance review.
- FastAPI engagement CRUD/public actions, Google session exchange, bearer sessions, and MongoDB indexes.
- Seeded Northstar Studio sample engagement at `checkpoint-demo`.
- Placeholder payment request state is visible after clearance; no payment processor is connected yet.

## Prioritized backlog
- P0: Create engagement form and agency engagement listing for authenticated users.
- P0: Complete public share-link routing from external URL.
- P1: Replace payment placeholder with Stripe payment links.
- P1: Add agency-side change-request review and milestone progression controls.
- P2: Email notifications, audit timeline export, and client branding.

## Next tasks
1. Add authenticated create-engagement flow with reorderable milestone editor.
2. Add payment provider integration and webhook reconciliation.
3. Add deep-link routing for arbitrary share tokens.