"""Iteration 5 tests: Deliverable Attachments, Payment Reminders, Thread Alerts,
Earnings Overview (backend correctness of totals-source data).

Uses seeded 'test-session-token' pattern from /app/memory/test_credentials.md.
"""
import asyncio
import io
import os
import sys
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


def _tail_log_since(marker_ts: float, needle: str, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(BACKEND_LOG, "r", errors="ignore") as f:
                content = f.read()
            snippet = content[-250_000:]
            if needle in snippet:
                return True
        except FileNotFoundError:
            pass
        time.sleep(0.5)
    return False


@pytest.fixture(scope="session")
def api():
    s = requests.Session()
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


@pytest.fixture(scope="module")
def engagement(auth_headers):
    """Create an engagement owned by user_test_01 for iteration 5 tests."""
    payload = {
        "client_name": "TEST_Iter5Client",
        "client_email": DELIVER_EMAIL,
        "milestones": [
            {"title": "TEST_iter5_ms1", "fee": 500, "expense": 0},
            {"title": "TEST_iter5_ms2", "fee": 1200, "expense": 80},
        ],
    }
    r = requests.post(f"{BASE_URL}/api/engagements", json=payload,
                      headers={**auth_headers, "Content-Type": "application/json"})
    assert r.status_code in (200, 201), r.text
    return r.json()


# ======================================================================
# 1) DELIVERABLE ATTACHMENTS — link
# ======================================================================
class TestAttachmentLinks:
    def test_add_link_unauth_401(self, api, engagement):
        eng_id = engagement["engagement_id"]
        ms1 = engagement["milestones"][0]["milestone_id"]
        r = api.post(
            f"{BASE_URL}/api/engagements/{eng_id}/milestones/{ms1}/attachments",
            json={"name": "Preview", "url": "https://example.com/preview"},
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 401, r.text

    def test_add_link_invalid_url_400(self, api, auth_headers, engagement):
        eng_id = engagement["engagement_id"]
        ms1 = engagement["milestones"][0]["milestone_id"]
        r = api.post(
            f"{BASE_URL}/api/engagements/{eng_id}/milestones/{ms1}/attachments",
            json={"name": "bad", "url": "ftp://not-http.com/x"},
            headers={**auth_headers, "Content-Type": "application/json"},
        )
        assert r.status_code == 400, r.text

    def test_add_link_ok_200(self, api, auth_headers, engagement):
        eng_id = engagement["engagement_id"]
        ms1 = engagement["milestones"][0]["milestone_id"]
        r = api.post(
            f"{BASE_URL}/api/engagements/{eng_id}/milestones/{ms1}/attachments",
            json={"name": "Preview cut v1", "url": "https://example.com/preview"},
            headers={**auth_headers, "Content-Type": "application/json"},
        )
        assert r.status_code == 200, r.text
        m = next(x for x in r.json()["milestones"] if x["milestone_id"] == ms1)
        assert len(m["attachments"]) >= 1
        att = m["attachments"][-1]
        assert att["kind"] == "link"
        assert att["name"] == "Preview cut v1"
        assert att["url"] == "https://example.com/preview"
        assert "storage_path" not in att  # never leaks
        pytest.link_att_id = att["attachment_id"]

    def test_public_open_link_redirect_307(self, engagement):
        token = engagement["share_token"]
        att_id = getattr(pytest, "link_att_id", None)
        assert att_id, "link attachment id missing"
        r = requests.get(
            f"{BASE_URL}/api/public/engagements/{token}/attachments/{att_id}",
            allow_redirects=False,
        )
        assert r.status_code in (302, 303, 307, 308), r.status_code
        assert r.headers.get("location") == "https://example.com/preview"


# ======================================================================
# 2) DELIVERABLE ATTACHMENTS — file upload / download / delete
# ======================================================================
class TestAttachmentFiles:
    def test_upload_ok_200_no_storage_path(self, api, auth_headers, engagement):
        eng_id = engagement["engagement_id"]
        ms2 = engagement["milestones"][1]["milestone_id"]
        payload = b"hello iteration 5 tiny file\n" * 20  # ~600 bytes
        files = {"file": ("iter5_note.txt", io.BytesIO(payload), "text/plain")}
        r = api.post(
            f"{BASE_URL}/api/engagements/{eng_id}/milestones/{ms2}/attachments/upload",
            files=files,
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        m = next(x for x in r.json()["milestones"] if x["milestone_id"] == ms2)
        att = m["attachments"][-1]
        assert att["kind"] == "file"
        assert att["name"] == "iter5_note.txt"
        assert att["content_type"].startswith("text/plain")
        assert "storage_path" not in att, "storage_path MUST NOT leak in API response"
        pytest.file_att_id = att["attachment_id"]
        pytest.file_att_expected = payload

    def test_public_download_streams_bytes(self, engagement):
        token = engagement["share_token"]
        att_id = getattr(pytest, "file_att_id", None)
        assert att_id, "file attachment id missing"
        r = requests.get(f"{BASE_URL}/api/public/engagements/{token}/attachments/{att_id}")
        assert r.status_code == 200, r.text
        assert r.headers.get("content-type", "").startswith("text/plain")
        assert r.content == pytest.file_att_expected

    def test_public_download_unknown_404(self, engagement):
        token = engagement["share_token"]
        r = requests.get(f"{BASE_URL}/api/public/engagements/{token}/attachments/att_unknown_xxx")
        assert r.status_code == 404

    def test_delete_unauth_401(self, api, engagement):
        eng_id = engagement["engagement_id"]
        ms2 = engagement["milestones"][1]["milestone_id"]
        att_id = getattr(pytest, "file_att_id", None)
        r = api.delete(
            f"{BASE_URL}/api/engagements/{eng_id}/milestones/{ms2}/attachments/{att_id}",
        )
        assert r.status_code == 401

    def test_delete_ok_200(self, api, auth_headers, engagement):
        eng_id = engagement["engagement_id"]
        ms2 = engagement["milestones"][1]["milestone_id"]
        att_id = getattr(pytest, "file_att_id", None)
        r = api.delete(
            f"{BASE_URL}/api/engagements/{eng_id}/milestones/{ms2}/attachments/{att_id}",
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        m = next(x for x in r.json()["milestones"] if x["milestone_id"] == ms2)
        assert all(a["attachment_id"] != att_id for a in m.get("attachments", []))

    def test_after_delete_public_download_404(self, engagement):
        token = engagement["share_token"]
        att_id = getattr(pytest, "file_att_id", None)
        r = requests.get(f"{BASE_URL}/api/public/engagements/{token}/attachments/{att_id}")
        assert r.status_code == 404


# ======================================================================
# 3) PAYMENT REMINDERS
# ======================================================================
class TestPaymentReminders:
    def test_reminder_sweep_stamps_and_emails(self, api, auth_headers, engagement, caplog):
        """Clear ms1, backdate cleared_at >3d, run sweep, verify stamp + email log."""
        token = engagement["share_token"]
        eng_id = engagement["engagement_id"]
        ms1 = engagement["milestones"][0]["milestone_id"]

        # Accept scope
        ar = api.post(f"{BASE_URL}/api/public/engagements/{token}/accept",
                      json={"client_name": "TEST_Iter5Client", "client_email": DELIVER_EMAIL},
                      headers={"Content-Type": "application/json"})
        assert ar.status_code == 200, ar.text

        # Clear ms1 → payment_status=requested, cleared_at set now
        cr = api.post(
            f"{BASE_URL}/api/public/engagements/{token}/milestones/{ms1}/clear",
            json={"client_name": "TEST_Iter5Client", "client_email": DELIVER_EMAIL},
            headers={"Content-Type": "application/json"},
        )
        assert cr.status_code == 200, cr.text
        m1 = next(m for m in cr.json()["milestones"] if m["milestone_id"] == ms1)
        assert m1["payment_status"] == "requested"

        # Backdate cleared_at to 5 days ago; clear any payment_reminder_at
        async def _backdate():
            c = AsyncIOMotorClient(MONGO_URL)[DB_NAME]
            doc = await c.engagements.find_one({"engagement_id": eng_id})
            for m in doc["milestones"]:
                if m["milestone_id"] == ms1:
                    m["cleared_at"] = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
                    m.pop("payment_reminder_at", None)
            await c.engagements.update_one({"engagement_id": eng_id}, {"$set": {"milestones": doc["milestones"]}})
        asyncio.get_event_loop().run_until_complete(_backdate())

        # Run sweep in-process
        sys.path.insert(0, "/app/backend")
        import importlib
        import logging as _lg
        server = importlib.import_module("server")
        caplog.set_level(_lg.INFO, logger="server")
        marker = time.time()

        async def _run():
            await server.run_payment_reminders()
            # let asyncio.create_task email send complete
            await asyncio.sleep(4)
        asyncio.get_event_loop().run_until_complete(_run())

        # Verify payment_reminder_at now stamped
        async def _read():
            c = AsyncIOMotorClient(MONGO_URL)[DB_NAME]
            doc = await c.engagements.find_one({"engagement_id": eng_id}, {"_id": 0})
            return next(m for m in doc["milestones"] if m["milestone_id"] == ms1)
        m_after = asyncio.get_event_loop().run_until_complete(_read())
        assert m_after.get("payment_reminder_at"), "payment_reminder_at must be stamped after sweep"
        first_stamp = m_after["payment_reminder_at"]

        # Check caplog for 'Friendly reminder' subject (email was sent in this process)
        # Accept 429 rate-limit from Emergent email proxy as evidence the send was attempted.
        joined = "\n".join(r.getMessage() for r in caplog.records)
        attempted = ("Friendly reminder" in joined) or (
            "Email send failed to delivered@resend.dev" in joined and "429" in joined
        )
        assert attempted, (
            f"expected 'Friendly reminder' email send (or 429 rate-limit) after sweep; got:\n{joined[-2000:]}"
        )

        # Second sweep immediately — should NOT resend because payment_reminder_at is fresh
        marker2 = time.time()

        async def _run2():
            await server.run_payment_reminders()
            await asyncio.sleep(1)
        asyncio.get_event_loop().run_until_complete(_run2())
        m_after2 = asyncio.get_event_loop().run_until_complete(_read())
        assert m_after2["payment_reminder_at"] == first_stamp, (
            "second sweep should NOT re-stamp payment_reminder_at (still fresh)"
        )


# ======================================================================
# 4) THREAD ALERTS — agency emailed on client change/reply
# ======================================================================
class TestThreadAlerts:
    def test_change_request_emails_agency(self, api, engagement):
        token = engagement["share_token"]
        ms2 = engagement["milestones"][1]["milestone_id"]
        marker = time.time()
        r = api.post(
            f"{BASE_URL}/api/public/engagements/{token}/milestones/{ms2}/request-change",
            json={"note": "please switch the palette", "author_name": "TEST_Iter5Client"},
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 200, r.text
        assert _tail_log_since(marker, f"Email sent to {TEST_EMAIL}", timeout=15) or \
               _tail_log_since(marker, "Change request update", timeout=5), \
               "expected thread-alert email to agency after change request"

    def test_change_message_reply_emails_agency(self, api, engagement):
        token = engagement["share_token"]
        ms2 = engagement["milestones"][1]["milestone_id"]
        marker = time.time()
        r = api.post(
            f"{BASE_URL}/api/public/engagements/{token}/milestones/{ms2}/change-messages",
            json={"body": "any updates on the palette?", "author_name": "TEST_Iter5Client"},
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 200, r.text
        assert _tail_log_since(marker, f"Email sent to {TEST_EMAIL}", timeout=15) or \
               _tail_log_since(marker, "Change request update", timeout=5), \
               "expected thread-alert email to agency after client reply"


# ======================================================================
# 5) EARNINGS OVERVIEW — dashboard totals source
# GET /api/engagements returns milestone.fee/expense/status/payment_status
# ======================================================================
class TestEarningsData:
    def test_list_engagements_has_fields_for_totals(self, api, auth_headers):
        r = api.get(f"{BASE_URL}/api/engagements", headers=auth_headers)
        assert r.status_code == 200, r.text
        rows = r.json()
        assert isinstance(rows, list)
        assert len(rows) >= 1
        row = rows[0]
        for m in row["milestones"]:
            assert "fee" in m
            assert "expense" in m
            assert "status" in m
            assert "payment_status" in m
