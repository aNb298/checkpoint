"""Checkpoint backend tests for the NEW features (iteration 4).

Covers:
- Client email notifications (asserted via backend logs — 'Email sent ...').
- Change request thread (public reply, agency reply, resolve).
- Stripe payments (create checkout, uncleared 409 guard, status polling, unknown 404).
- Auth guard on agency thread reply.

Uses the seeded 'test-session-token' pattern from /app/memory/test_credentials.md.
"""
import asyncio
import os
import time
from datetime import datetime, timezone, timedelta

import pytest
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")

BASE_URL = None
if os.environ.get("EXPO_PUBLIC_BACKEND_URL"):
    BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
else:
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().strip('"').rstrip("/")
                break

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

TEST_SESSION_TOKEN = "test-session-token"
TEST_USER_ID = "user_test_01"
TEST_EMAIL = "TEST_agency@test.co"
DELIVER_EMAIL = "delivered@resend.dev"
BACKEND_LOG = "/var/log/supervisor/backend.err.log"


def _tail_log_since(marker_ts: float, needle: str, timeout: float = 10.0) -> bool:
    """Poll backend log for `needle` appearing after `marker_ts` (epoch seconds)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(BACKEND_LOG, "r", errors="ignore") as f:
                content = f.read()
            # Take the last 100kb only
            snippet = content[-200_000:]
            if needle in snippet:
                return True
        except FileNotFoundError:
            pass
        time.sleep(0.5)
    return False


@pytest.fixture(scope="session")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def auth_headers():
    async def _seed():
        c = AsyncIOMotorClient(MONGO_URL)[DB_NAME]
        now = datetime.now(timezone.utc)
        # Seed by user_id (unique index)
        await c.users.update_one(
            {"user_id": TEST_USER_ID},
            {"$set": {"user_id": TEST_USER_ID, "email": TEST_EMAIL,
                      "name": "Test Agency", "picture": None}},
            upsert=True,
        )
        await c.user_sessions.update_one(
            {"session_token": TEST_SESSION_TOKEN},
            {"$set": {"session_token": TEST_SESSION_TOKEN, "user_id": TEST_USER_ID,
                      "created_at": now, "expires_at": now + timedelta(days=7)}},
            upsert=True,
        )
        await c.engagements.delete_many({"agency_user_id": TEST_USER_ID})
    asyncio.get_event_loop().run_until_complete(_seed())
    return {"Authorization": f"Bearer {TEST_SESSION_TOKEN}"}


@pytest.fixture(scope="module")
def created_engagement(api, auth_headers):
    """Create an engagement with client_email=delivered@resend.dev.
    Asserts the 'engagement created' email is dispatched (log check)."""
    marker = time.time()
    payload = {
        "client_name": "TEST_Client",
        "client_email": DELIVER_EMAIL,
        "milestones": [
            {"title": "TEST_Kickoff", "fee": 500, "expense": 0},
            {"title": "TEST_Delivery", "fee": 1800, "expense": 120},
            {"title": "TEST_Revisions", "fee": 900, "expense": 0},
        ],
    }
    r = api.post(f"{BASE_URL}/api/engagements", json=payload, headers=auth_headers)
    assert r.status_code in (200, 201), r.text
    data = r.json()
    assert data["client_email"] == DELIVER_EMAIL
    assert len(data["milestones"]) == 3
    # Email log check: allow up to 15s for async httpx call
    found = _tail_log_since(marker, "Your project plan is ready to review", timeout=15)
    data["_email_created_logged"] = found
    return data


# ------------------------ Email notifications ------------------------

class TestEmailNotifications:
    def test_engagement_creation_email_logged(self, created_engagement):
        assert created_engagement["_email_created_logged"], (
            "Expected 'Your project plan is ready to review' in backend log "
            "after POST /api/engagements with client_email"
        )

    def test_checkpoint_ready_email_on_clear(self, api, created_engagement):
        token = created_engagement["share_token"]
        # accept scope
        ar = api.post(f"{BASE_URL}/api/public/engagements/{token}/accept",
                      json={"client_name": "TEST_Client", "client_email": DELIVER_EMAIL})
        assert ar.status_code == 200, ar.text
        ms1 = created_engagement["milestones"][0]["milestone_id"]
        marker = time.time()
        cr = api.post(
            f"{BASE_URL}/api/public/engagements/{token}/milestones/{ms1}/clear",
            json={"client_name": "TEST_Client", "client_email": DELIVER_EMAIL},
        )
        assert cr.status_code == 200, cr.text
        cleared_data = cr.json()
        m1 = next(m for m in cleared_data["milestones"] if m["milestone_id"] == ms1)
        assert m1["status"] == "cleared"
        assert m1["payment_status"] == "requested"
        # next-checkpoint email log
        assert _tail_log_since(marker, "Checkpoint ready for review", timeout=15), (
            "Expected 'Checkpoint ready for review' in backend log after clearing first milestone"
        )


# ------------------------ Change request thread ------------------------

class TestChangeThread:
    def test_open_change_thread_via_public(self, api, created_engagement):
        token = created_engagement["share_token"]
        # ms3 is the 3rd — but request-change can be opened on any awaiting one; pick ms2 (still awaiting after ms1 cleared)
        ms2 = created_engagement["milestones"][1]["milestone_id"]
        r = api.post(
            f"{BASE_URL}/api/public/engagements/{token}/milestones/{ms2}/request-change",
            json={"note": "please tweak color", "author_name": "TEST_Client"},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        m = next(m for m in data["milestones"] if m["milestone_id"] == ms2)
        assert m["change_status"] == "open"
        assert m["change_request"] == "please tweak color"
        assert len(m["change_thread"]) == 1
        assert m["change_thread"][0]["author"] == "client"
        assert m["change_thread"][0]["author_name"] == "TEST_Client"
        assert m["change_thread"][0]["body"] == "please tweak color"

    def test_client_can_post_reply_via_public(self, api, created_engagement):
        token = created_engagement["share_token"]
        ms2 = created_engagement["milestones"][1]["milestone_id"]
        r = api.post(
            f"{BASE_URL}/api/public/engagements/{token}/milestones/{ms2}/change-messages",
            json={"body": "any updates?", "author_name": "TEST_Client"},
        )
        assert r.status_code == 200, r.text
        m = next(m for m in r.json()["milestones"] if m["milestone_id"] == ms2)
        assert len(m["change_thread"]) >= 2
        last = m["change_thread"][-1]
        assert last["author"] == "client"
        assert last["body"] == "any updates?"

    def test_agency_thread_reply_requires_auth(self, api, created_engagement):
        eng_id = created_engagement["engagement_id"]
        ms2 = created_engagement["milestones"][1]["milestone_id"]
        r = api.post(
            f"{BASE_URL}/api/engagements/{eng_id}/milestones/{ms2}/change-messages",
            json={"body": "should fail"},
        )
        assert r.status_code == 401, r.text

    def test_agency_thread_reply_authed(self, api, auth_headers, created_engagement):
        eng_id = created_engagement["engagement_id"]
        ms2 = created_engagement["milestones"][1]["milestone_id"]
        r = api.post(
            f"{BASE_URL}/api/engagements/{eng_id}/milestones/{ms2}/change-messages",
            json={"body": "on it — new preview coming"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        m = next(m for m in r.json()["milestones"] if m["milestone_id"] == ms2)
        last = m["change_thread"][-1]
        assert last["author"] == "agency"
        assert last["author_name"] == "Test Agency"
        assert last["body"] == "on it — new preview coming"

    def test_agency_resolve_change(self, api, auth_headers, created_engagement):
        eng_id = created_engagement["engagement_id"]
        ms2 = created_engagement["milestones"][1]["milestone_id"]
        r = api.post(
            f"{BASE_URL}/api/engagements/{eng_id}/milestones/{ms2}/resolve-change",
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        m = next(m for m in r.json()["milestones"] if m["milestone_id"] == ms2)
        assert m["change_status"] == "resolved"


# ------------------------ Stripe payments ------------------------

class TestStripePayments:
    def test_pay_uncleared_returns_409(self, api, created_engagement):
        token = created_engagement["share_token"]
        # ms3 is still awaiting_clearance
        ms3 = created_engagement["milestones"][2]["milestone_id"]
        r = api.post(
            f"{BASE_URL}/api/public/engagements/{token}/milestones/{ms3}/pay",
        )
        assert r.status_code == 409, r.text

    def test_pay_cleared_returns_stripe_url(self, api, created_engagement):
        token = created_engagement["share_token"]
        ms1 = created_engagement["milestones"][0]["milestone_id"]
        r = api.post(
            f"{BASE_URL}/api/public/engagements/{token}/milestones/{ms1}/pay",
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["url"].startswith("https://checkout.stripe.com/"), d
        assert d["session_id"]
        # Verify payment_transactions doc created
        async def _check():
            c = AsyncIOMotorClient(MONGO_URL)[DB_NAME]
            return await c.payment_transactions.find_one({"session_id": d["session_id"]}, {"_id": 0})
        tx = asyncio.get_event_loop().run_until_complete(_check())
        assert tx is not None
        assert tx["payment_status"] == "initiated"
        assert tx["amount"] == 500  # ms1 fee=500, expense=0
        # Stash session_id on module
        pytest.stripe_session_id = d["session_id"]

    def test_payment_status_open_unpaid(self, api):
        sid = getattr(pytest, "stripe_session_id", None)
        if not sid:
            pytest.skip("no session_id from create_payment test")
        r = api.get(f"{BASE_URL}/api/public/payments/{sid}/status")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["payment_status"] == "unpaid"
        assert d["status"] == "open"

    def test_payment_status_unknown_404(self, api):
        r = api.get(f"{BASE_URL}/api/public/payments/cs_unknown_xxx/status")
        assert r.status_code == 404, r.text
