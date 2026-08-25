import os
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL")


def api_url(path):
    return f"{BASE_URL.rstrip('/')}/api{path}"


def test_seeded_engagement_shape():
    response = requests.get(api_url("/public/engagements/checkpoint-demo"), timeout=15)
    assert response.status_code == 200
    data = response.json()
    assert data["client_name"] == "Northstar Studio"
    assert len(data["milestones"]) == 6
    assert sum(m["status"] == "cleared" for m in data["milestones"]) == 2
    assert data["scope_accepted_at"]


def test_missing_public_engagement_is_404():
    response = requests.get(api_url("/public/engagements/does-not-exist"), timeout=15)
    assert response.status_code == 404
    assert response.json()["detail"] == "Engagement not found"


def test_seeded_clearance_generates_payment_request():
    initial = requests.get(api_url("/public/engagements/checkpoint-demo"), timeout=15).json()
    pending = next(m for m in initial["milestones"] if m["status"] != "cleared")
    response = requests.post(
        api_url(f"/public/engagements/checkpoint-demo/milestones/{pending['milestone_id']}/clear"),
        timeout=15,
    )
    assert response.status_code == 200
    cleared = next(m for m in response.json()["milestones"] if m["milestone_id"] == pending["milestone_id"])
    assert cleared["status"] == "cleared"
    assert cleared["payment_status"] == "requested"