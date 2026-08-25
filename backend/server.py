from fastapi import FastAPI, APIRouter, HTTPException, Header
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
from datetime import datetime, timezone, timedelta
import httpx


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")


# Define Models
class StatusCheck(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class StatusCheckCreate(BaseModel):
    client_name: str

class Milestone(BaseModel):
    milestone_id: str = Field(default_factory=lambda: f"ms_{uuid.uuid4().hex[:10]}")
    title: str
    fee: float
    expense: float = 0
    status: str = "awaiting_clearance"
    payment_status: str = "not_requested"
    change_request: Optional[str] = None
    cleared_by_name: Optional[str] = None
    cleared_by_email: Optional[str] = None
    cleared_at: Optional[str] = None

class EngagementCreate(BaseModel):
    client_name: str
    client_email: Optional[str] = None
    milestones: List[Milestone]

class Engagement(BaseModel):
    engagement_id: str
    client_name: str
    client_email: Optional[str] = None
    share_token: str
    status: str = "awaiting_scope_acceptance"
    scope_accepted_at: Optional[str] = None
    milestones: List[Milestone]
    created_at: str

class ScopeAcceptance(BaseModel):
    client_name: Optional[str] = None
    client_email: Optional[str] = None

class ChangeRequest(BaseModel):
    note: str

class ClearMilestone(BaseModel):
    client_name: Optional[str] = None
    client_email: Optional[str] = None

class User(BaseModel):
    user_id: str
    email: str
    name: str
    picture: Optional[str] = None

# Add your routes to the router instead of directly to app
@api_router.get("/")
async def root():
    return {"message": "Checkpoint API"}

def clean_engagement(doc):
    doc.pop("_id", None)
    return doc

async def current_user(authorization: Optional[str]):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    token = authorization[7:]
    session = await db.user_sessions.find_one({"session_token": token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    expires = session["expires_at"]
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expired")
    user = await db.users.find_one({"user_id": session["user_id"]}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

@api_router.post("/auth/session")
async def exchange_session(payload: dict):
    session_id = payload.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    async with httpx.AsyncClient(timeout=15) as http:
        response = await http.get("https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data", headers={"X-Session-ID": session_id})
    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired Google session")
    data = response.json()
    user_data = data.get("user") or data
    email = user_data.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="Google profile missing email")
    existing = await db.users.find_one({"email": email}, {"_id": 0})
    user_id = existing["user_id"] if existing else f"user_{uuid.uuid4().hex[:12]}"
    user = {"user_id": user_id, "email": email, "name": user_data.get("name") or email.split("@")[0], "picture": user_data.get("picture")}
    await db.users.update_one({"email": email}, {"$set": user}, upsert=True)
    token = data.get("session_token") or user_data.get("session_token")
    if not token:
        raise HTTPException(status_code=401, detail="Google session token missing")
    now = datetime.now(timezone.utc)
    await db.user_sessions.update_one({"session_token": token}, {"$set": {"session_token": token, "user_id": user_id, "created_at": now, "expires_at": now + timedelta(days=7)}}, upsert=True)
    return {"session_token": token, "user": user}

@api_router.get("/auth/me", response_model=User)
async def auth_me(authorization: Optional[str] = Header(default=None)):
    return await current_user(authorization)

@api_router.get("/engagements", response_model=List[Engagement])
async def list_engagements(authorization: Optional[str] = Header(default=None)):
    user = await current_user(authorization)
    docs = await db.engagements.find({"agency_user_id": user["user_id"]}, {"_id": 0}).sort("created_at", -1).to_list(100)
    return [Engagement(**clean_engagement(d)) for d in docs]

@api_router.post("/engagements", response_model=Engagement)
async def create_engagement(payload: EngagementCreate, authorization: Optional[str] = Header(default=None)):
    user = await current_user(authorization)
    doc = payload.model_dump()
    doc.update({"engagement_id": f"eng_{uuid.uuid4().hex[:10]}", "agency_user_id": user["user_id"], "share_token": uuid.uuid4().hex, "status": "awaiting_scope_acceptance", "scope_accepted_at": None, "created_at": datetime.now(timezone.utc).isoformat()})
    await db.engagements.insert_one(doc)
    return Engagement(**clean_engagement(doc))

async def get_engagement(token: str):
    doc = await db.engagements.find_one({"share_token": token}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Engagement not found")
    return doc

@api_router.get("/public/engagements/{token}", response_model=Engagement)
async def public_engagement(token: str):
    return Engagement(**await get_engagement(token))

@api_router.post("/public/engagements/{token}/accept", response_model=Engagement)
async def accept_scope(token: str, payload: ScopeAcceptance):
    doc = await get_engagement(token)
    accepted = datetime.now(timezone.utc).isoformat()
    await db.engagements.update_one({"share_token": token}, {"$set": {"scope_accepted_at": accepted, "status": "active", "client_name": payload.client_name or doc["client_name"], "client_email": payload.client_email or doc.get("client_email")}})
    await db.scope_acceptance_events.insert_one({"audit_id": f"audit_{uuid.uuid4().hex[:12]}", "engagement_id": doc["engagement_id"], "share_token": token, "client_name": payload.client_name or doc["client_name"], "client_email": payload.client_email or doc.get("client_email"), "accepted_at": accepted})
    doc.update({"scope_accepted_at": accepted, "status": "active", "client_name": payload.client_name or doc["client_name"], "client_email": payload.client_email or doc.get("client_email")})
    return Engagement(**clean_engagement(doc))

@api_router.post("/public/engagements/{token}/milestones/{milestone_id}/clear", response_model=Engagement)
async def clear_milestone(token: str, milestone_id: str, payload: ClearMilestone):
    doc = await get_engagement(token)
    if doc.get("status") != "active":
        raise HTTPException(status_code=409, detail="Scope must be accepted first")
    changed = False
    cleared_at = datetime.now(timezone.utc).isoformat()
    fallback_name = doc.get("client_name") or "Client"
    fallback_email = doc.get("client_email")
    for index, milestone in enumerate(doc["milestones"]):
        if milestone["milestone_id"] == milestone_id:
            if index > 0 and doc["milestones"][index - 1]["status"] != "cleared":
                raise HTTPException(status_code=409, detail="Clear the previous checkpoint first")
            milestone["status"] = "cleared"
            milestone["payment_status"] = "requested"
            milestone["cleared_by_name"] = (payload.client_name or "").strip() or fallback_name
            milestone["cleared_by_email"] = (payload.client_email or "").strip() or fallback_email
            milestone["cleared_at"] = cleared_at
            changed = True
            break
    if not changed:
        raise HTTPException(status_code=404, detail="Milestone not found")
    await db.engagements.update_one({"share_token": token}, {"$set": {"milestones": doc["milestones"]}})
    return Engagement(**clean_engagement(doc))

@api_router.post("/public/engagements/{token}/milestones/{milestone_id}/request-change", response_model=Engagement)
async def request_change(token: str, milestone_id: str, payload: ChangeRequest):
    doc = await get_engagement(token)
    for milestone in doc["milestones"]:
        if milestone["milestone_id"] == milestone_id:
            milestone["change_request"] = payload.note
            break
    else:
        raise HTTPException(status_code=404, detail="Milestone not found")
    await db.engagements.update_one({"share_token": token}, {"$set": {"milestones": doc["milestones"]}})
    return Engagement(**clean_engagement(doc))

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_dict = input.dict()
    status_obj = StatusCheck(**status_dict)
    _ = await db.status_checks.insert_one(status_obj.dict())
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    status_checks = await db.status_checks.find().to_list(1000)
    return [StatusCheck(**status_check) for status_check in status_checks]

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

@app.on_event("startup")
async def ensure_indexes_and_sample():
    await db.users.create_index("email", unique=True)
    await db.users.create_index("user_id", unique=True)
    await db.user_sessions.create_index("session_token", unique=True)
    await db.user_sessions.create_index("expires_at", expireAfterSeconds=0)
    if await db.engagements.count_documents({}) == 0:
        sample = {"engagement_id": "eng_sample_01", "agency_user_id": "sample_agency", "client_name": "Northstar Studio", "client_email": "hello@northstar.example", "share_token": "checkpoint-demo", "status": "active", "scope_accepted_at": "2025-02-14T10:30:00+00:00", "created_at": "2025-02-10T09:00:00+00:00", "milestones": [{"milestone_id": "ms_01", "title": "Creative direction", "fee": 900, "expense": 0, "status": "cleared", "payment_status": "paid", "change_request": None, "cleared_by_name": "Ava Chen", "cleared_by_email": "ava@northstar.example", "cleared_at": "2025-02-16T14:12:00+00:00"}, {"milestone_id": "ms_02", "title": "First cut delivery", "fee": 1800, "expense": 120, "status": "cleared", "payment_status": "requested", "change_request": None, "cleared_by_name": "Ava Chen", "cleared_by_email": "ava@northstar.example", "cleared_at": "2025-02-22T09:04:00+00:00"}, {"milestone_id": "ms_03", "title": "Revision round", "fee": 1100, "expense": 0, "status": "awaiting_clearance", "payment_status": "not_requested", "change_request": None}, {"milestone_id": "ms_04", "title": "Final masters", "fee": 700, "expense": 80, "status": "awaiting_clearance", "payment_status": "not_requested", "change_request": None}, {"milestone_id": "ms_05", "title": "Launch handoff", "fee": 500, "expense": 0, "status": "awaiting_clearance", "payment_status": "not_requested", "change_request": None}, {"milestone_id": "ms_06", "title": "Archive & closeout", "fee": 300, "expense": 0, "status": "awaiting_clearance", "payment_status": "not_requested", "change_request": None}]}
        await db.engagements.insert_one(sample)
        await db.scope_acceptance_events.insert_one({"audit_id": "audit_sample_01", "engagement_id": "eng_sample_01", "share_token": "checkpoint-demo", "client_name": "Northstar Studio", "client_email": "hello@northstar.example", "accepted_at": "2025-02-14T10:30:00+00:00"})
    elif not await db.scope_acceptance_events.find_one({"engagement_id": "eng_sample_01"}, {"_id": 0}):
        await db.scope_acceptance_events.insert_one({"audit_id": "audit_sample_01", "engagement_id": "eng_sample_01", "share_token": "checkpoint-demo", "client_name": "Northstar Studio", "client_email": "hello@northstar.example", "accepted_at": "2025-02-14T10:30:00+00:00"})
    # Backfill cleared_by fields on legacy sample data
    sample_doc = await db.engagements.find_one({"engagement_id": "eng_sample_01"}, {"_id": 0})
    if sample_doc and any(m.get("status") == "cleared" and not m.get("cleared_by_name") for m in sample_doc.get("milestones", [])):
        for m in sample_doc["milestones"]:
            if m.get("status") == "cleared" and not m.get("cleared_by_name"):
                m["cleared_by_name"] = "Ava Chen"
                m["cleared_by_email"] = "ava@northstar.example"
                m["cleared_at"] = m.get("cleared_at") or "2025-02-20T12:00:00+00:00"
        await db.engagements.update_one({"engagement_id": "eng_sample_01"}, {"$set": {"milestones": sample_doc["milestones"]}})
