"""Checkpoint backend regression tests.

Covers:
- Public share-token engagement retrieval, sequential milestone clearance,
  change requests.
- Authenticated agency endpoints via a seeded DB session (per
  /app/memory/test_credentials.md).
- Scope-acceptance flip.
"""
import asyncio
import os
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

DEMO_TOKEN = "checkpoint-demo"
TEST_SESSION_TOKEN = "test-session-token"
TEST_USER_ID = "user_test_01"
TEST_EMAIL = "TEST_agency@test.co"


@pytest.fixture(scope="session")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def auth_headers():
    """Seed a DB session per test_credentials.md and return auth headers."""
    async def _seed():
        c = AsyncIOMotorClient(MONGO_URL)[DB_NAME]
        now = datetime.now(timezone.utc)
        await c.users.update_one(
            {"email": TEST_EMAIL},
            {"$set": {"user_id": TEST_USER_ID, "email": TEST_EMAIL, "name": "TEST Agency", "picture": None}},
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


# ---------------------- Public / demo engagement ----------------------

class TestPublicDemo:
    def test_get_demo_engagement_has_cleared_by(self, api):
        r = api.get(f"{BASE_URL}/api/public/engagements/{DEMO_TOKEN}")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["share_token"] == DEMO_TOKEN
        milestones = {m["milestone_id"]: m for m in data["milestones"]}
        for mid in ("ms_01", "ms_02"):
            m = milestones[mid]
            assert m["status"] == "cleared"
            assert m["cleared_by_name"] == "Ava Chen"
            assert m["cleared_by_email"] == "ava@northstar.example"
            assert m["cleared_at"]

    def test_sequential_clear_returns_409(self, api):
        r = api.post(
            f"{BASE_URL}/api/public/engagements/{DEMO_TOKEN}/milestones/ms_05/clear",
            json={"client_name": "Test", "client_email": "t@t.co"},
        )
        assert r.status_code == 409, r.text

    def test_clear_ms03_sets_priya_and_payment_requested(self, api):
        r = api.post(
            f"{BASE_URL}/api/public/engagements/{DEMO_TOKEN}/milestones/ms_03/clear",
            json={"client_name": "Priya", "client_email": "priya@t.co"},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        ms3 = next(m for m in data["milestones"] if m["milestone_id"] == "ms_03")
        assert ms3["status"] == "cleared"
        assert ms3["cleared_by_name"] == "Priya"
        assert ms3["cleared_by_email"] == "priya@t.co"
        assert ms3["cleared_at"] is not None
        assert ms3["payment_status"] == "requested"

    def test_request_change_sets_note(self, api):
        r = api.post(
            f"{BASE_URL}/api/public/engagements/{DEMO_TOKEN}/milestones/ms_04/request-change",
            json={"note": "change x"},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        ms4 = next(m for m in data["milestones"] if m["milestone_id"] == "ms_04")
        assert ms4["change_request"] == "change x"


# ---------------------- Auth ----------------------

class TestAuth:
    def test_me_without_auth_401(self, api):
        r = api.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 401, r.text

    def test_me_with_seeded_session_200(self, api, auth_headers):
        r = api.get(f"{BASE_URL}/api/auth/me", headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["email"] == TEST_EMAIL


# ---------------------- Authenticated engagements ----------------------

class TestAgencyEngagements:
    def test_initial_list_empty(self, api, auth_headers):
        r = api.get(f"{BASE_URL}/api/engagements", headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json() == []

    def test_create_and_list(self, api, auth_headers):
        payload = {
            "client_name": "Acme Co",
            "client_email": "ops@acme.co",
            "milestones": [
                {"title": "Kickoff", "fee": 500, "expense": 0},
                {"title": "Delivery", "fee": 1500, "expense": 100},
            ],
        }
        r = api.post(f"{BASE_URL}/api/engagements", json=payload, headers=auth_headers)
        assert r.status_code in (200, 201), r.text
        created = r.json()
        assert created["engagement_id"]
        assert created["share_token"]
        assert created["status"] == "awaiting_scope_acceptance"
        assert len(created["milestones"]) == 2
        pytest.new_share_token = created["share_token"]

        r2 = api.get(f"{BASE_URL}/api/engagements", headers=auth_headers)
        assert r2.status_code == 200
        lst = r2.json()
        assert any(e["engagement_id"] == created["engagement_id"] for e in lst)

    def test_accept_scope_flips_to_active(self, api):
        tok = getattr(pytest, "new_share_token", None)
        if not tok:
            pytest.skip("no share token from create test")
        r = api.post(
            f"{BASE_URL}/api/public/engagements/{tok}/accept",
            json={"client_name": "X", "client_email": "x@y.co"},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "active"
        assert data["scope_accepted_at"] is not None
        r2 = requests.get(f"{BASE_URL}/api/public/engagements/{tok}")
        assert r2.status_code == 200
        assert r2.json()["status"] == "active"
