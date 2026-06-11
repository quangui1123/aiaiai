import logging
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.models import (
    ChatRequest, TokenCreateRequest, TokenUpdateRequest, QuotaAddRequest,
    ChannelRequest, ChannelUpdateRequest, ModelRequest,
    RegisterRequest, LoginRequest, PasswordChangeRequest,
    TokenCreateUserRequest, UserUpdateRequest,
)
from app import repositories as repo
from app.services import (
    create_jwt, decode_jwt, openai_error, handle_chat_completion,
    handle_list_models, handle_public_models, handle_public_status, read_html,
)
from app.config import settings

logger = logging.getLogger("ai-proxy")
security = HTTPBearer(auto_error=False)

router = APIRouter()
admin_router = APIRouter()
auth_router = APIRouter()
v1_router = APIRouter()
public_router = APIRouter()


# ── Dependencies ────────────────────────────────────

def extract_bearer_token(authorization: str | None, credentials: HTTPAuthorizationCredentials | None) -> str | None:
    if credentials and credentials.credentials:
        return credentials.credentials
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        return authorization.strip()
    return None


async def get_token_user(request, credentials=Depends(security)):
    token = extract_bearer_token(request.headers.get("authorization"), credentials)
    if not token:
        raise HTTPException(401, detail={"error": {"message": "Missing authorization header", "type": "invalid_request_error"}})
    td = await repo.verify_token(token)
    if not td:
        raise HTTPException(401, detail={"error": {"message": "Invalid or revoked token, or quota exceeded", "type": "invalid_request_error"}})
    return td


async def get_current_user(authorization: str | None = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing authorization header")
    payload = decode_jwt(authorization.split(" ", 1)[1])
    if not payload:
        raise HTTPException(401, "Invalid or expired token")
    user = await repo.get_user_by_id(int(payload["sub"]))
    if not user or not user["enabled"]:
        raise HTTPException(401, "User not found or disabled")
    return user


async def get_admin_user(user=Depends(get_current_user)):
    if not user.get("is_admin"):
        raise HTTPException(403, "Admin access required")
    return user


def _user_payload(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "is_admin": bool(user["is_admin"]),
        "quota": user["quota"],
        "used_quota": user["used_quota"],
        "rate_limit_rpm": user["rate_limit_rpm"],
    }


# ── v1 Router (OpenAI-compatible) ───────────────────

@v1_router.post("/chat/completions")
async def chat_completions(req: ChatRequest, request=None, td=Depends(get_token_user)):
    return await handle_chat_completion(req, td)


@v1_router.get("/models")
async def list_models(td=Depends(get_token_user)):
    return await handle_list_models(td)


# ── Auth Router ─────────────────────────────────────

@auth_router.post("/register")
async def auth_register(body: RegisterRequest):
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Invalid email")
    if len(body.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    existing = await repo.get_user_by_email(email, include_disabled=True)
    if existing:
        raise HTTPException(400, "Email already registered")

    user = await repo.create_user(email=email, password=body.password, name=body.name or email.split("@")[0], is_admin=False, quota=settings.default_quota)
    if not user:
        raise HTTPException(400, "Registration failed")

    td = await repo.create_token(name=f"{user['name']}'s Token", email=email, role="user", quota=-1, rate_limit_rpm=30, user_id=user["id"])
    jwt_token = create_jwt(user["id"], user["email"], bool(user["is_admin"]))
    return {"ok": True, "token": jwt_token, "user": _user_payload(user), "api_token": td["token"]}


@auth_router.post("/login")
async def auth_login(body: LoginRequest):
    email = body.email.strip().lower()
    user = await repo.get_user_by_email(email, include_disabled=True)
    if not user or not repo.verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Invalid email or password")
    if not user["enabled"]:
        raise HTTPException(403, "Account disabled")
    jwt_token = create_jwt(user["id"], user["email"], bool(user["is_admin"]))
    return {"ok": True, "token": jwt_token, "user": _user_payload(user)}


@auth_router.get("/me")
async def auth_me(user=Depends(get_current_user)):
    tokens = await repo.list_tokens(user_id=user["id"])
    return {"user": {**_user_payload(user), "enabled": bool(user["enabled"]), "created_at": user["created_at"]}, "tokens": tokens}


@auth_router.patch("/password")
async def auth_change_password(body: PasswordChangeRequest, user=Depends(get_current_user)):
    if not repo.verify_password(body.old_password, user["password_hash"]):
        raise HTTPException(400, "Old password is incorrect")
    if len(body.new_password) < 6:
        raise HTTPException(400, "New password must be at least 6 characters")
    await repo.update_user_password(user["id"], body.new_password)
    return {"ok": True}


@auth_router.post("/tokens")
async def user_create_token(body: TokenCreateUserRequest, user=Depends(get_current_user)):
    td = await repo.create_token(name=body.name or f"{user['name']}'s Token", email=user["email"], role="user", quota=-1, rate_limit_rpm=user.get("rate_limit_rpm") or 30, user_id=user["id"])
    return {"ok": True, "token": td}


@auth_router.get("/tokens")
async def user_list_tokens(user=Depends(get_current_user)):
    return {"tokens": await repo.list_tokens(user_id=user["id"])}


@auth_router.delete("/tokens/{tid}")
async def user_delete_token(tid: int, user=Depends(get_current_user)):
    t = await repo.get_token_by_id(tid, user_id=user["id"])
    if not t:
        raise HTTPException(404, "Token not found")
    await repo.revoke_token(tid)
    return {"ok": True}


@auth_router.get("/usage")
async def user_usage(user=Depends(get_current_user)):
    return {"stats": await repo.get_usage_stats(user_id=user["id"]), "logs": await repo.get_recent_logs(limit=50, user_id=user["id"])}


# ── Admin Router ────────────────────────────────────

@admin_router.get("/stats")
async def api_stats(days: int = 30, admin=Depends(get_admin_user)):
    return await repo.get_usage_stats(days)


@admin_router.get("/logs")
async def api_logs(limit: int = 100, admin=Depends(get_admin_user)):
    return {"logs": await repo.get_recent_logs(limit)}


@admin_router.get("/users")
async def api_users(admin=Depends(get_admin_user)):
    users = await repo.list_users()
    for u in users:
        u["token_count"] = len(await repo.list_tokens(user_id=u["id"]))
    return {"users": users}


@admin_router.patch("/users/{uid}")
async def api_update_user(uid: int, body: UserUpdateRequest, admin=Depends(get_admin_user)):
    await repo.update_user(uid, **body.model_dump(exclude_none=True))
    return {"ok": True}


@admin_router.post("/users/{uid}/quota")
async def api_add_user_quota(uid: int, body: QuotaAddRequest, admin=Depends(get_admin_user)):
    await repo.add_user_quota(uid, body.amount)
    return {"ok": True}


@admin_router.get("/users/{uid}/tokens")
async def api_user_tokens(uid: int, admin=Depends(get_admin_user)):
    return {"tokens": await repo.list_tokens(user_id=uid)}


@admin_router.get("/users/{uid}/usage")
async def api_user_usage(uid: int, admin=Depends(get_admin_user)):
    return {"stats": await repo.get_usage_stats(user_id=uid), "logs": await repo.get_recent_logs(limit=50, user_id=uid)}


@admin_router.get("/providers")
async def api_providers(admin=Depends(get_admin_user)):
    providers = await repo.get_providers()
    models_list = await repo.get_models()
    configured = await repo.get_configured_provider_ids()
    for p in providers:
        p["channel_count"] = len([c for c in await repo.get_channels(p["id"]) if c.get("enabled", 1)])
        p["model_count"] = len([m for m in models_list if m["provider_id"] == p["id"]])
        p["configured"] = p["id"] in configured
    return {"providers": providers}


@admin_router.get("/channels")
async def api_channels(provider_id: str | None = None, admin=Depends(get_admin_user)):
    return {"channels": await repo.get_channels(provider_id)}


@admin_router.post("/channels")
async def api_add_channel(body: ChannelRequest, admin=Depends(get_admin_user)):
    if not body.api_key.strip():
        raise HTTPException(400, "API key is required")
    await repo.add_channel(body.provider_id, body.name, body.api_key.strip(), body.weight)
    return {"ok": True}


@admin_router.patch("/channels/{cid}")
async def api_update_channel(cid: int, body: ChannelUpdateRequest, admin=Depends(get_admin_user)):
    await repo.update_channel(cid, **body.model_dump(exclude_none=True))
    return {"ok": True}


@admin_router.delete("/channels/{cid}")
async def api_delete_channel(cid: int, admin=Depends(get_admin_user)):
    await repo.delete_channel(cid)
    return {"ok": True}


@admin_router.get("/models")
async def api_models(provider_id: str | None = None, admin=Depends(get_admin_user)):
    return {"models": await repo.get_models(provider_id)}


@admin_router.post("/models")
async def api_add_model(body: ModelRequest, admin=Depends(get_admin_user)):
    await repo.add_model(body.model_id, body.provider_id, body.display_name, body.input_price, body.output_price, body.unit_size)
    return {"ok": True}


@admin_router.delete("/models/{provider_id}/{model_id}")
async def api_delete_model(provider_id: str, model_id: str, admin=Depends(get_admin_user)):
    await repo.delete_model(model_id, provider_id)
    return {"ok": True}


@admin_router.get("/tokens")
async def api_tokens(admin=Depends(get_admin_user)):
    return {"tokens": await repo.list_tokens()}


@admin_router.post("/tokens")
async def api_create_token(body: TokenCreateRequest, admin=Depends(get_admin_user)):
    td = await repo.create_token(body.name, body.email, body.role, body.quota, body.rate_limit_rpm)
    return {"ok": True, "token": td}


@admin_router.patch("/tokens/{tid}")
async def api_update_token(tid: int, body: TokenUpdateRequest, admin=Depends(get_admin_user)):
    await repo.update_token(tid, **body.model_dump(exclude_none=True))
    return {"ok": True}


@admin_router.delete("/tokens/{tid}")
async def api_revoke_token(tid: int, admin=Depends(get_admin_user)):
    await repo.revoke_token(tid)
    return {"ok": True}


@admin_router.post("/tokens/{tid}/quota")
async def api_add_quota(tid: int, body: QuotaAddRequest, admin=Depends(get_admin_user)):
    await repo.add_quota(tid, body.amount)
    return {"ok": True}


# ── Public Router ───────────────────────────────────

@public_router.get("/models")
async def public_models():
    return await handle_public_models()


@public_router.get("/status")
async def public_status():
    return await handle_public_status()


# ── Page Routes ─────────────────────────────────────

@router.get("/admin")
async def admin_panel():
    return read_html("admin.html")


@router.get("/pricing")
async def pricing_page():
    return read_html("pricing.html")


@router.get("/login")
async def login_page():
    return read_html("login.html")


@router.get("/dashboard")
async def dashboard_page():
    return read_html("dashboard.html")


@router.get("/")
async def index():
    return read_html("index.html")


@router.get("/health")
async def health():
    return {"status": "ok"}
