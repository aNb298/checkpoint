from fastapi import FastAPI, APIRouter, HTTPException, Header, Request
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
import re
import ipaddress
import asyncio
from datetime import datetime, timezone, timedelta
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse
import httpx
from emergentintegrations.payments.stripe.checkout import StripeCheckout, CheckoutSessionRequest


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

logger = logging.getLogger(__name__)

# ---- Emergent managed email (Resend proxy) + Stripe config ----
EMAIL_BASE_URL = "https://integrations.emergentagent.com"  # constant, never from env
EMAIL_KEY = os.environ.get("EMERGENT_EMAIL_KEY", "")
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "Checkpoint")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "").rstrip("/")
STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY", "")

# ---- Email guardrail gate (G2/G3 structural checks) ----
_SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "is.gd", "cutt.ly", "goo.gl", "rebrand.ly")
_CRED_ASK = ("reply with your password", "reply with the code", "send your password", "cvv",
             "send us your password", "enter your password below", "confirm your card number",
             "your full card number", "seed phrase", "recovery phrase", "verify your card",
             "social security number", "confirm your bank details")
_HOSTISH = re.compile(r"\b(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})", re.I)

def _host_ok(host: str) -> bool:
    if not host or "xn--" in host:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return not any(host == s or host.endswith("." + s) for s in _SHORTENERS)

def _same_site(shown: str, real: str) -> bool:
    return shown == real or real.endswith("." + shown) or shown.endswith("." + real)

class _EmailScan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.urls, self.anchors = set(), [], []
        self._href, self._text = None, []
    def handle_starttag(self, tag, attrs):
        self.tags.add(tag.lower())
        self.urls += [v for k, v in attrs if k.lower() in ("href", "src") and v]
        if tag.lower() == "a":
            self._href = dict((k.lower(), v) for k, v in attrs).get("href")
            self._text = []
    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)
    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, "".join(self._text)))
            self._href, self._text = None, []

def _assert_safe_email(subject: str, html: str) -> None:
    scan = _EmailScan(); scan.feed(html)
    if scan.tags & {"form", "input", "textarea", "select"}:
        raise ValueError("No forms or input fields in email (G2)")
    body = f"{subject}\n{html}".lower()
    for p in _CRED_ASK:
        if p in body:
            raise ValueError(f"Email asks the recipient for credentials: {p!r} (G2)")
    for url in scan.urls:
        low = url.strip().lower()
        if low.startswith(("mailto:", "tel:", "cid:", "#")):
            continue
        if not low.startswith("https://"):
            raise ValueError(f"Email links/assets must be absolute https: {url!r} (G3)")
        host = urlparse(low).hostname or ""
        if not _host_ok(host) or urlparse(low).username is not None:
            raise ValueError(f"Shortened, numeric-host or credential-bearing URL: {url!r} (G3)")
    for href, text in scan.anchors:
        real = urlparse(href.strip().lower()).hostname or ""
        if not real:
            continue
        for m in _HOSTISH.finditer(text):
            if not _same_site(m.group(1).lower(), real):
                raise ValueError(f"Anchor text {m.group(1)!r} != real link host {real!r} (G3)")

async def _deliver_email(to: str, subject: str, html: str):
    """Background email send — logs failures, never raises into a request path."""
    try:
        _assert_safe_email(subject, html)
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(f"{EMAIL_BASE_URL}/api/v1/email/send", headers={"X-Email-Key": EMAIL_KEY}, json={"to": [to], "subject": subject, "html": html, "from_name": EMAIL_FROM_NAME})
        resp.raise_for_status()
        logger.info(f"Email sent to {to}: {subject!r}")
    except Exception as e:
        logger.error(f"Email send failed to {to}: {e}")

def _email_shell(inner: str) -> str:
    return (
        '<table role="presentation" width="100%" style="background:#17151D;padding:36px 0"><tr><td align="center">'
        '<table role="presentation" width="520" style="background:#221E2B;border-radius:14px;padding:34px;font-family:Arial,Helvetica,sans-serif">'
        f'<tr><td><p style="color:#9B6CFF;font-size:11px;letter-spacing:2px;font-weight:bold;margin:0 0 18px">CHECKPOINT</p>{inner}'
        f'<p style="font-size:11px;color:#9A93A6;margin-top:30px">Sent by {escape(EMAIL_FROM_NAME)}. We never ask for your password or card details by email.</p>'
        '</td></tr></table></td></tr></table>'
    )

def _portal_button(token: str, label: str) -> str:
    return (f'<p style="margin:26px 0 0"><a href="{APP_BASE_URL}/{token}" style="background:#9B6CFF;color:#F6F4F8;text-decoration:none;'
            f'padding:14px 26px;border-radius:8px;font-size:14px;font-weight:bold;display:inline-block">{escape(label)}</a></p>')

async def send_engagement_created_email(doc: dict):
    total = sum(float(m.get("fee") or 0) + float(m.get("expense") or 0) for m in doc.get("milestones", []))
    count = len(doc.get("milestones", []))
    inner = (
        f'<h2 style="color:#F6F4F8;font-size:22px;margin:0">Hi {escape(doc.get("client_name") or "there")}, your project plan is ready.</h2>'
        f'<p style="color:#C2B8D5;font-size:14px;line-height:22px;margin:16px 0 0">A new engagement has been set up for you on Checkpoint with '
        f'<strong style="color:#F6F4F8">{count} checkpoint{"s" if count != 1 else ""}</strong> totaling '
        f'<strong style="color:#F6F4F8">${total:,.0f}</strong>. Review the full trajectory and accept the scope to unlock the first checkpoint.</p>'
        + _portal_button(doc["share_token"], "Review your project plan")
    )
    await _deliver_email(doc["client_email"], "Your project plan is ready to review on Checkpoint", _email_shell(inner))

async def send_checkpoint_ready_email(doc: dict, milestone: dict, position: int):
    inner = (
        f'<h2 style="color:#F6F4F8;font-size:22px;margin:0">Checkpoint {position:02d} is ready for your review.</h2>'
        f'<p style="color:#C2B8D5;font-size:14px;line-height:22px;margin:16px 0 0"><strong style="color:#F6F4F8">{escape(milestone["title"])}</strong> '
        f'(${float(milestone.get("fee") or 0):,.0f} fee) is now the active checkpoint on your engagement. '
        f'Open your portal to review the deliverable, clear it, or request changes.</p>'
        + _portal_button(doc["share_token"], "Open your client portal")
    )
    await _deliver_email(doc["client_email"], f'Checkpoint ready for review: {milestone["title"]}', _email_shell(inner))

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

class ThreadMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:10]}")
    author: str
    author_name: Optional[str] = None
    body: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class ThreadMessageCreate(BaseModel):
    body: str
    author_name: Optional[str] = None

class Milestone(BaseModel):
    milestone_id: str = Field(default_factory=lambda: f"ms_{uuid.uuid4().hex[:10]}")
    title: str
    fee: float
    expense: float = 0
    status: str = "awaiting_clearance"
    payment_status: str = "not_requested"
    change_request: Optional[str] = None
    change_status: Optional[str] = None
    change_thread: List[ThreadMessage] = Field(default_factory=list)
    payment_session_id: Optional[str] = None
    paid_at: Optional[str] = None
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
    author_name: Optional[str] = None

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
    clean_engagement(doc)
    if doc.get("client_email"):
        asyncio.create_task(send_engagement_created_email(doc))
    return Engagement(**doc)

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
    if doc.get("client_email"):
        next_ms = next((m for m in doc["milestones"] if m["status"] != "cleared"), None)
        if next_ms:
            position = doc["milestones"].index(next_ms) + 1
            asyncio.create_task(send_checkpoint_ready_email(doc, next_ms, position))
    return Engagement(**clean_engagement(doc))

@api_router.post("/public/engagements/{token}/milestones/{milestone_id}/request-change", response_model=Engagement)
async def request_change(token: str, milestone_id: str, payload: ChangeRequest):
    doc = await get_engagement(token)
    for milestone in doc["milestones"]:
        if milestone["milestone_id"] == milestone_id:
            milestone["change_request"] = payload.note
            milestone["change_status"] = "open"
            milestone.setdefault("change_thread", [])
            milestone["change_thread"].append(ThreadMessage(author="client", author_name=payload.author_name or doc.get("client_name"), body=payload.note).model_dump())
            break
    else:
        raise HTTPException(status_code=404, detail="Milestone not found")
    await db.engagements.update_one({"share_token": token}, {"$set": {"milestones": doc["milestones"]}})
    return Engagement(**clean_engagement(doc))

def _find_milestone(doc, milestone_id):
    for m in doc["milestones"]:
        if m["milestone_id"] == milestone_id:
            return m
    raise HTTPException(status_code=404, detail="Milestone not found")

async def get_agency_engagement(engagement_id: str, user):
    doc = await db.engagements.find_one({"engagement_id": engagement_id, "agency_user_id": user["user_id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Engagement not found")
    return doc

@api_router.post("/public/engagements/{token}/milestones/{milestone_id}/change-messages", response_model=Engagement)
async def client_change_message(token: str, milestone_id: str, payload: ThreadMessageCreate):
    if not payload.body.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    doc = await get_engagement(token)
    milestone = _find_milestone(doc, milestone_id)
    milestone.setdefault("change_thread", [])
    milestone["change_thread"].append(ThreadMessage(author="client", author_name=payload.author_name or doc.get("client_name"), body=payload.body.strip()).model_dump())
    milestone["change_status"] = "open"
    await db.engagements.update_one({"share_token": token}, {"$set": {"milestones": doc["milestones"]}})
    return Engagement(**clean_engagement(doc))

@api_router.post("/engagements/{engagement_id}/milestones/{milestone_id}/change-messages", response_model=Engagement)
async def agency_change_message(engagement_id: str, milestone_id: str, payload: ThreadMessageCreate, authorization: Optional[str] = Header(default=None)):
    user = await current_user(authorization)
    if not payload.body.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    doc = await get_agency_engagement(engagement_id, user)
    milestone = _find_milestone(doc, milestone_id)
    milestone.setdefault("change_thread", [])
    milestone["change_thread"].append(ThreadMessage(author="agency", author_name=user.get("name"), body=payload.body.strip()).model_dump())
    if not milestone.get("change_status"):
        milestone["change_status"] = "open"
    await db.engagements.update_one({"engagement_id": engagement_id}, {"$set": {"milestones": doc["milestones"]}})
    return Engagement(**clean_engagement(doc))

@api_router.post("/engagements/{engagement_id}/milestones/{milestone_id}/resolve-change", response_model=Engagement)
async def resolve_change(engagement_id: str, milestone_id: str, authorization: Optional[str] = Header(default=None)):
    user = await current_user(authorization)
    doc = await get_agency_engagement(engagement_id, user)
    milestone = _find_milestone(doc, milestone_id)
    milestone["change_status"] = "resolved"
    await db.engagements.update_one({"engagement_id": engagement_id}, {"$set": {"milestones": doc["milestones"]}})
    return Engagement(**clean_engagement(doc))

# ---- Stripe payments ----
def _stripe():
    if not STRIPE_API_KEY:
        raise HTTPException(status_code=503, detail="Payments not configured")
    return StripeCheckout(api_key=STRIPE_API_KEY, webhook_url=f"{APP_BASE_URL}/api/webhook/stripe")

@api_router.post("/public/engagements/{token}/milestones/{milestone_id}/pay")
async def create_milestone_payment(token: str, milestone_id: str):
    doc = await get_engagement(token)
    milestone = _find_milestone(doc, milestone_id)
    if milestone["status"] != "cleared":
        raise HTTPException(status_code=409, detail="Milestone must be cleared before payment")
    if milestone.get("payment_status") == "paid":
        raise HTTPException(status_code=409, detail="Milestone already paid")
    amount = float(milestone["fee"]) + float(milestone.get("expense") or 0)
    session = await _stripe().create_checkout_session(CheckoutSessionRequest(
        amount=amount,
        currency="usd",
        success_url=f"{APP_BASE_URL}/{token}?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{APP_BASE_URL}/{token}",
        metadata={"engagement_id": doc["engagement_id"], "milestone_id": milestone_id, "share_token": token},
    ))
    await db.payment_transactions.insert_one({"session_id": session.session_id, "engagement_id": doc["engagement_id"], "milestone_id": milestone_id, "share_token": token, "amount": amount, "currency": "usd", "payment_status": "initiated", "created_at": datetime.now(timezone.utc).isoformat()})
    milestone["payment_session_id"] = session.session_id
    await db.engagements.update_one({"share_token": token}, {"$set": {"milestones": doc["milestones"]}})
    return {"url": session.url, "session_id": session.session_id}

async def _mark_paid(session_id: str):
    tx = await db.payment_transactions.find_one({"session_id": session_id}, {"_id": 0})
    if not tx or tx.get("payment_status") == "paid":
        return
    paid_at = datetime.now(timezone.utc).isoformat()
    result = await db.payment_transactions.update_one({"session_id": session_id, "payment_status": {"$ne": "paid"}}, {"$set": {"payment_status": "paid", "paid_at": paid_at}})
    if result.modified_count == 0:
        return
    doc = await db.engagements.find_one({"engagement_id": tx["engagement_id"]}, {"_id": 0})
    if not doc:
        return
    for m in doc["milestones"]:
        if m["milestone_id"] == tx["milestone_id"]:
            m["payment_status"] = "paid"
            m["paid_at"] = paid_at
    await db.engagements.update_one({"engagement_id": tx["engagement_id"]}, {"$set": {"milestones": doc["milestones"]}})

@api_router.get("/public/payments/{session_id}/status")
async def payment_status(session_id: str):
    tx = await db.payment_transactions.find_one({"session_id": session_id}, {"_id": 0})
    if not tx:
        raise HTTPException(status_code=404, detail="Payment not found")
    if tx.get("payment_status") == "paid":
        return {"status": "complete", "payment_status": "paid"}
    checkout = await _stripe().get_checkout_status(session_id)
    if checkout.payment_status == "paid":
        await _mark_paid(session_id)
    return {"status": checkout.status, "payment_status": checkout.payment_status}

@api_router.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("Stripe-Signature")
    try:
        event = await _stripe().handle_webhook(body, signature)
    except Exception as e:
        logger.error(f"Stripe webhook error: {e}")
        raise HTTPException(status_code=400, detail="Invalid webhook")
    if getattr(event, "payment_status", None) == "paid" and getattr(event, "session_id", None):
        await _mark_paid(event.session_id)
    return {"received": True}

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
