"""Iteration 6 backend tests:
- Milestone editing (edit / add / move / delete) with pre-scope guard
- Archive/unarchive flow + guards
- Client receipt email on _mark_paid
- PDF summary endpoint
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


def _tail_log_since(marker_ts: float, needle: str, timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(BACKEND_LOG, "r", errors="ignore") as f:
                content = f.read()
            if needle in content[-300_000:]:
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


def _create_engagement(api, auth_headers, client_email=None):
    payload = {
        "client_name": "TEST_Iter6",
        "client_email": client_email,
        "milestones": [
            {"title": "TEST_A", "fee": 500, "expense": 0},
            {"title": "TEST_B", "fee": 800, "expense": 50},
            {"title": "TEST_C", "fee": 1000, "expense": 0},
        ],
    }
    r = api.post(f"{BASE_URL}/api/engagements", json=payload, headers=auth_headers)
    assert r.status_code in (200, 201), r.text
    return r.json()


# ============ Milestone editing ============

class TestMilestoneEditing:
    def test_edit_milestone_success(self, api, auth_headers):
        eng = _create_engagement(api, auth_headers)
        eid = eng["engagement_id"]
        ms1 = eng["milestones"][0]["milestone_id"]
        r = api.put(
            f"{BASE_URL}/api/engagements/{eid}/milestones/{ms1}",
            json={"title": "TEST_edited", "fee": 750, "expense": 25},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        m = next(m for m in r.json()["milestones"] if m["milestone_id"] == ms1)
        assert m["title"] == "TEST_edited"
        assert m["fee"] == 750
        assert m["expense"] == 25

    def test_edit_milestone_fee_zero_returns_400(self, api, auth_headers):
        eng = _create_engagement(api, auth_headers)
        eid = eng["engagement_id"]
        ms1 = eng["milestones"][0]["milestone_id"]
        r = api.put(
            f"{BASE_URL}/api/engagements/{eid}/milestones/{ms1}",
            json={"fee": 0},
            headers=auth_headers,
        )
        assert r.status_code == 400, r.text

    def test_edit_milestone_unauth_returns_401(self, api, auth_headers):
        eng = _create_engagement(api, auth_headers)
        eid = eng["engagement_id"]
        ms1 = eng["milestones"][0]["milestone_id"]
        r = api.put(
            f"{BASE_URL}/api/engagements/{eid}/milestones/{ms1}",
            json={"title": "X"},
        )
        assert r.status_code == 401, r.text

    def test_add_milestone_appends(self, api, auth_headers):
        eng = _create_engagement(api, auth_headers)
        eid = eng["engagement_id"]
        before = len(eng["milestones"])
        r = api.post(
            f"{BASE_URL}/api/engagements/{eid}/milestones",
            json={"title": "TEST_appended", "fee": 250, "expense": 0},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert len(data["milestones"]) == before + 1
        assert data["milestones"][-1]["title"] == "TEST_appended"
        assert data["milestones"][-1]["fee"] == 250

    def test_move_milestone_up_reorders(self, api, auth_headers):
        eng = _create_engagement(api, auth_headers)
        eid = eng["engagement_id"]
        ms_ids = [m["milestone_id"] for m in eng["milestones"]]
        # move index 1 up -> should be first
        r = api.post(
            f"{BASE_URL}/api/engagements/{eid}/milestones/{ms_ids[1]}/move",
            json={"direction": "up"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        new_order = [m["milestone_id"] for m in r.json()["milestones"]]
        assert new_order[0] == ms_ids[1]
        assert new_order[1] == ms_ids[0]
        assert new_order[2] == ms_ids[2]

    def test_delete_milestone_success(self, api, auth_headers):
        eng = _create_engagement(api, auth_headers)
        eid = eng["engagement_id"]
        ms2 = eng["milestones"][1]["milestone_id"]
        r = api.delete(
            f"{BASE_URL}/api/engagements/{eid}/milestones/{ms2}",
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        remaining = [m["milestone_id"] for m in r.json()["milestones"]]
        assert ms2 not in remaining
        assert len(remaining) == 2

    def test_delete_last_milestone_returns_409(self, api, auth_headers):
        # Create engagement with a single milestone
        payload = {
            "client_name": "TEST_singleton",
            "client_email": None,
            "milestones": [{"title": "solo", "fee": 100, "expense": 0}],
        }
        r = api.post(f"{BASE_URL}/api/engagements", json=payload, headers=auth_headers)
        assert r.status_code in (200, 201)
        eng = r.json()
        eid = eng["engagement_id"]
        ms1 = eng["milestones"][0]["milestone_id"]
        rd = api.delete(
            f"{BASE_URL}/api/engagements/{eid}/milestones/{ms1}",
            headers=auth_headers,
        )
        assert rd.status_code == 409, rd.text

    def test_editing_after_accept_returns_409(self, api, auth_headers):
        eng = _create_engagement(api, auth_headers)
        eid = eng["engagement_id"]
        token = eng["share_token"]
        ms1 = eng["milestones"][0]["milestone_id"]
        # accept scope
        ar = api.post(
            f"{BASE_URL}/api/public/engagements/{token}/accept",
            json={"client_name": "C", "client_email": DELIVER_EMAIL},
        )
        assert ar.status_code == 200, ar.text
        # attempt edit
        r_edit = api.put(
            f"{BASE_URL}/api/engagements/{eid}/milestones/{ms1}",
            json={"title": "post-accept"},
            headers=auth_headers,
        )
        assert r_edit.status_code == 409, r_edit.text
        # add
        r_add = api.post(
            f"{BASE_URL}/api/engagements/{eid}/milestones",
            json={"title": "new", "fee": 50},
            headers=auth_headers,
        )
        assert r_add.status_code == 409, r_add.text
        # delete
        r_del = api.delete(
            f"{BASE_URL}/api/engagements/{eid}/milestones/{ms1}",
            headers=auth_headers,
        )
        assert r_del.status_code == 409, r_del.text
        # move
        r_mv = api.post(
            f"{BASE_URL}/api/engagements/{eid}/milestones/{ms1}/move",
            json={"direction": "down"},
            headers=auth_headers,
        )
        assert r_mv.status_code == 409, r_mv.text


# ============ Archive/unarchive ============

class TestArchiveUnarchive:
    def test_archive_sets_status(self, api, auth_headers):
        eng = _create_engagement(api, auth_headers)
        eid = eng["engagement_id"]
        r = api.post(f"{BASE_URL}/api/engagements/{eid}/archive", headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "archived"

    def test_client_accept_on_archived_returns_409(self, api, auth_headers):
        eng = _create_engagement(api, auth_headers)
        eid = eng["engagement_id"]
        token = eng["share_token"]
        ra = api.post(f"{BASE_URL}/api/engagements/{eid}/archive", headers=auth_headers)
        assert ra.status_code == 200
        r = api.post(
            f"{BASE_URL}/api/public/engagements/{token}/accept",
            json={"client_name": "C", "client_email": DELIVER_EMAIL},
        )
        assert r.status_code == 409, r.text

    def test_editing_while_archived_returns_409(self, api, auth_headers):
        eng = _create_engagement(api, auth_headers)
        eid = eng["engagement_id"]
        ms1 = eng["milestones"][0]["milestone_id"]
        ra = api.post(f"{BASE_URL}/api/engagements/{eid}/archive", headers=auth_headers)
        assert ra.status_code == 200
        r = api.put(
            f"{BASE_URL}/api/engagements/{eid}/milestones/{ms1}",
            json={"title": "should-fail"},
            headers=auth_headers,
        )
        assert r.status_code == 409, r.text

    def test_unarchive_returns_awaiting_when_never_accepted(self, api, auth_headers):
        eng = _create_engagement(api, auth_headers)
        eid = eng["engagement_id"]
        api.post(f"{BASE_URL}/api/engagements/{eid}/archive", headers=auth_headers)
        r = api.post(f"{BASE_URL}/api/engagements/{eid}/unarchive", headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "awaiting_scope_acceptance"

    def test_unarchive_returns_active_when_accepted(self, api, auth_headers):
        eng = _create_engagement(api, auth_headers)
        eid = eng["engagement_id"]
        token = eng["share_token"]
        ar = api.post(
            f"{BASE_URL}/api/public/engagements/{token}/accept",
            json={"client_name": "C", "client_email": DELIVER_EMAIL},
        )
        assert ar.status_code == 200
        api.post(f"{BASE_URL}/api/engagements/{eid}/archive", headers=auth_headers)
        r = api.post(f"{BASE_URL}/api/engagements/{eid}/unarchive", headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "active"


# ============ Receipts via _mark_paid ============

class TestReceipts:
    def test_receipt_email_logged_after_mark_paid(self, api, auth_headers, caplog):
        # Set up: create engagement with delivered@resend.dev, accept, clear ms1
        eng = _create_engagement(api, auth_headers, client_email=DELIVER_EMAIL)
        eid = eng["engagement_id"]
        token = eng["share_token"]
        ms1 = eng["milestones"][0]["milestone_id"]
        ar = api.post(
            f"{BASE_URL}/api/public/engagements/{token}/accept",
            json={"client_name": "TEST_Iter6", "client_email": DELIVER_EMAIL},
        )
        assert ar.status_code == 200, ar.text
        cr = api.post(
            f"{BASE_URL}/api/public/engagements/{token}/milestones/{ms1}/clear",
            json={"client_name": "TEST_Iter6", "client_email": DELIVER_EMAIL},
        )
        assert cr.status_code == 200, cr.text

        # Insert payment_transactions doc and call server._mark_paid in-process
        session_id = f"test_rcpt_{int(time.time())}"

        async def _run():
            c = AsyncIOMotorClient(MONGO_URL)[DB_NAME]
            await c.payment_transactions.insert_one({
                "session_id": session_id,
                "engagement_id": eid,
                "milestone_id": ms1,
                "payment_status": "initiated",
                "amount": 500,
                "currency": "usd",
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            import sys
            sys.path.insert(0, "/app/backend")
            import server  # noqa: E402
            marker = time.time()
            await server._mark_paid(session_id)
            # allow the fire-and-forget receipt email task to run
            await asyncio.sleep(5)
            return marker

        import logging
        caplog.set_level(logging.INFO)
        marker = asyncio.get_event_loop().run_until_complete(_run())

        # Verify DB milestone flipped to paid
        async def _check():
            c = AsyncIOMotorClient(MONGO_URL)[DB_NAME]
            doc = await c.engagements.find_one({"engagement_id": eid}, {"_id": 0})
            return doc

        doc = asyncio.get_event_loop().run_until_complete(_check())
        m1 = next(m for m in doc["milestones"] if m["milestone_id"] == ms1)
        assert m1["payment_status"] == "paid", m1

        # Log check for the receipt line. In-process logs live in caplog (not
        # supervisor logs), plus a network 429 from Emergent email proxy is
        # acceptable evidence of a real send attempt per iter5 pattern.
        log_text = " ".join(r.getMessage() for r in caplog.records)
        assert (
            "Receipt:" in log_text
            or "receipt" in log_text.lower()
            or "429" in log_text
        ), f"Expected 'Receipt:' evidence in in-process log. Captured:\n{log_text[-2000:]}"


# ============ PDF ============

class TestPdfSummary:
    def test_pdf_ok(self, api):
        r = api.get(f"{BASE_URL}/api/public/engagements/checkpoint-demo/summary.pdf")
        assert r.status_code == 200, r.text
        assert r.headers.get("Content-Type", "").startswith("application/pdf")
        assert r.content[:4] == b"%PDF"

    def test_pdf_unknown_token_404(self, api):
        r = api.get(f"{BASE_URL}/api/public/engagements/does-not-exist-xyz/summary.pdf")
        assert r.status_code == 404, r.text
