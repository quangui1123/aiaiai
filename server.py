import os, uuid, time, httpx, jwt
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from database import (
    init_db, create_token, verify_token, list_tokens, revoke_token, update_token, add_quota,
    check_rate_limit, log_usage, get_usage_stats, get_recent_logs,
    get_providers, get_channels, add_channel, delete_channel, update_channel,
    get_channel_key, get_models, get_model_price, add_model, delete_model,
    create_user, get_user_by_email, get_user_by_id, list_users, update_user, update_user_password, get_token_by_id,
    verify_password, check_user_quota, add_user_quota
)
from models import (
    ChatRequest, ChatResponse, ModelList, ModelItem,
    TokenCreateRequest, TokenUpdateRequest, QuotaAddRequest,
    ChannelRequest, ChannelUpdateRequest, ModelRequest,
    RegisterRequest, LoginRequest, AuthResponse, PasswordChangeRequest,
    TokenCreateUserRequest, UserUpdateRequest
)
from providers import create_adapter

load_dotenv()
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@aiaiai.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
JWT_SECRET = os.getenv("JWT_SECRET", "")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
DEFAULT_QUOTA = float(os.getenv("DEFAULT_QUOTA", "0.5"))

# Ensure JWT_SECRET is set
import secrets as _secrets
if not JWT_SECRET:
    JWT_SECRET = _secrets.token_urlsafe(32)

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 168  # 7 days


def create_jwt(user_id: int, email: str, is_admin: bool) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "is_admin": is_admin,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Ensure admin user exists
    for i in range(5):
        admin = get_user_by_email(ADMIN_EMAIL)
        if admin:
            break
        try:
            create_user(
                email=ADMIN_EMAIL,
                password=ADMIN_PASSWORD,
                name="Admin",
                is_admin=True,
                quota=-1  # unlimited
            )
            break
        except Exception:
            import time
            time.sleep(0.5)
    yield


app = FastAPI(title="AI Token Proxy", version="2.1.0", lifespan=lifespan)
security = HTTPBearer(auto_error=False)
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── Auth dependencies ──────────────────────────────

async def get_token_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)):
    """Auth by API token (sk-xxx). Used for /v1/* endpoints."""
    if not credentials:
        raise HTTPException(401, "Missing authorization header")
    td = verify_token(credentials.credentials)
    if not td:
        raise HTTPException(401, "Invalid or revoked token, or quota exceeded")
    return td


async def get_current_user(authorization: str | None = Header(None)):
    """Auth by JWT. Used for web dashboard endpoints."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing authorization header")
    token = authorization.split(" ", 1)[1]
    payload = decode_jwt(token)
    if not payload:
        raise HTTPException(401, "Invalid or expired token")
    user = get_user_by_id(int(payload["sub"]))
    if not user or not user["enabled"]:
        raise HTTPException(401, "User not found or disabled")
    return user


async def get_admin_user(user: dict = Depends(get_current_user)):
    """Require admin role."""
    if not user.get("is_admin"):
        raise HTTPException(403, "Admin access required")
    return user


def admin_required(x_admin_key: str | None = Header(None)):
    """Legacy admin key auth for backward compat."""
    if x_admin_key != ADMIN_PASSWORD:
        raise HTTPException(403, "Invalid admin key")
    return True


# ── Pricing ────────────────────────────────────────

def calc_cost(model_id, prompt_tokens, completion_tokens):
    price = get_model_price(model_id)
    if not price:
        return 0
    units = price["unit_size"]
    in_cost = (prompt_tokens / units) * price["input_price"]
    out_cost = (completion_tokens / units) * price["output_price"]
    return round(in_cost + out_cost, 6)


def resolve_provider(model_id: str) -> str | None:
    models = get_models()
    for m in models:
        if m["id"] == model_id:
            return m["provider_id"]
    for m in sorted(models, key=lambda x: -len(x["id"])):
        if model_id.startswith(m["id"]):
            return m["provider_id"]
    return None


# ── Chat completions ──────────────────────────────

@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest, td: dict = Depends(get_token_user)):
    tid = td["id"]
    uname = td["name"]
    rpm = td["rate_limit_rpm"]

    if not check_rate_limit(tid, rpm):
        raise HTTPException(429, "Rate limit exceeded")

    provider_id = req.provider or resolve_provider(req.model)
    if not provider_id:
        raise HTTPException(400, f"Unknown model: {req.model}")

    api_key, channel_id = get_channel_key(provider_id)
    if not api_key:
        raise HTTPException(503, f"Provider '{provider_id}' has no configured channels")

    providers = get_providers()
    pinfo = next((p for p in providers if p["id"] == provider_id), None)
    if not pinfo:
        raise HTTPException(503, f"Provider '{provider_id}' not found")

    try:
        adapter = await create_adapter(provider_id, api_key, pinfo["base_url"])
    except Exception as e:
        await log_usage(tid, uname, provider_id, req.model, 0, 0, 0, 0, "error")
        raise HTTPException(502, str(e))

    try:
        if req.stream:
            async def generate():
                try:
                    async for chunk in adapter.chat_stream(req):
                        yield chunk
                except Exception:
                    pass
                finally:
                    await log_usage(tid, uname, provider_id, req.model, 0, 0, 0, 0, "success")
                    await adapter.close()
            return StreamingResponse(generate(), media_type="text/event-stream")
        else:
            resp = await adapter.chat(req)
            cost = calc_cost(req.model, resp.usage.prompt_tokens, resp.usage.completion_tokens)
            await log_usage(tid, uname, provider_id, req.model,
                            resp.usage.prompt_tokens, resp.usage.completion_tokens,
                            resp.usage.total_tokens, cost, "success")
            await adapter.close()
            return resp
    except httpx.HTTPStatusError as e:
        await adapter.close()
        await log_usage(tid, uname, provider_id, req.model, 0, 0, 0, 0, "error")
        detail = ""
        try:
            detail = e.response.json()
        except Exception:
            detail = e.response.text[:500] if e.response.text else ""
        raise HTTPException(502, detail=f"Upstream {e.response.status_code}: {detail}")
    except Exception as e:
        await adapter.close()
        await log_usage(tid, uname, provider_id, req.model, 0, 0, 0, 0, "error")
        raise HTTPException(502, detail=str(e))


# ── Models ────────────────────────────────────────

@app.get("/v1/models")
async def list_models(td: dict = Depends(get_token_user)):
    models = get_models()
    data = [
        ModelItem(id=m["id"], created=int(time.time()), owned_by=m["provider_name"])
        for m in models
    ]
    return ModelList(data=data)


# ── Pages ─────────────────────────────────────────

@app.get("/admin")
async def admin_panel():
    p = os.path.join(STATIC_DIR, "admin.html")
    return HTMLResponse(open(p, encoding="utf-8").read()) if os.path.exists(p) else HTMLResponse("<h1>Admin panel not found</h1>", 404)


@app.get("/pricing")
async def pricing_page():
    p = os.path.join(STATIC_DIR, "pricing.html")
    return HTMLResponse(open(p, encoding="utf-8").read()) if os.path.exists(p) else HTMLResponse("<h1>Pricing page not found</h1>", 404)


@app.get("/login")
async def login_page():
    p = os.path.join(STATIC_DIR, "login.html")
    return HTMLResponse(open(p, encoding="utf-8").read()) if os.path.exists(p) else HTMLResponse("<h1>Login page not found</h1>", 404)


@app.get("/dashboard")
async def dashboard_page():
    p = os.path.join(STATIC_DIR, "dashboard.html")
    return HTMLResponse(open(p, encoding="utf-8").read()) if os.path.exists(p) else HTMLResponse("<h1>Dashboard not found</h1>", 404)


@app.get("/")
async def index():
    p = os.path.join(STATIC_DIR, "index.html")
    return HTMLResponse(open(p, encoding="utf-8").read()) if os.path.exists(p) else HTMLResponse("<h1>Index not found</h1>", 404)


# ── Auth endpoints ────────────────────────────────

@app.post("/api/auth/register")
async def auth_register(body: RegisterRequest):
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Invalid email")
    if len(body.password) < 4:
        raise HTTPException(400, "Password must be at least 4 characters")

    existing = get_user_by_email(email)
    if existing:
        raise HTTPException(400, "Email already registered")

    is_admin = (email == ADMIN_EMAIL.strip().lower())
    user = create_user(email=email, password=body.password, name=body.name or email.split("@")[0], is_admin=is_admin, quota=DEFAULT_QUOTA)
    if not user:
        raise HTTPException(400, "Registration failed")

    # Auto-create first API token
    td = create_token(name=f"{user['name']}'s Token", email=email, role="user", quota=-1, rate_limit_rpm=30, user_id=user["id"])

    jwt_token = create_jwt(user["id"], user["email"], bool(user["is_admin"]))
    return {
        "ok": True,
        "token": jwt_token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user["name"],
            "is_admin": bool(user["is_admin"]),
            "quota": user["quota"],
            "used_quota": user["used_quota"],
            "rate_limit_rpm": user["rate_limit_rpm"],
        },
        "api_token": td["token"]
    }


@app.post("/api/auth/login")
async def auth_login(body: LoginRequest):
    email = body.email.strip().lower()
    user = get_user_by_email(email)
    if not user:
        raise HTTPException(401, "Invalid email or password")

    if not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Invalid email or password")

    if not user["enabled"]:
        raise HTTPException(403, "Account disabled")

    jwt_token = create_jwt(user["id"], user["email"], bool(user["is_admin"]))
    return {
        "ok": True,
        "token": jwt_token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user["name"],
            "is_admin": bool(user["is_admin"]),
            "quota": user["quota"],
            "used_quota": user["used_quota"],
            "rate_limit_rpm": user["rate_limit_rpm"],
        }
    }


@app.get("/api/auth/me")
async def auth_me(user: dict = Depends(get_current_user)):
    tokens = list_tokens(user_id=user["id"])
    return {
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user["name"],
            "is_admin": bool(user["is_admin"]),
            "quota": user["quota"],
            "used_quota": user["used_quota"],
            "rate_limit_rpm": user["rate_limit_rpm"],
            "enabled": bool(user["enabled"]),
            "created_at": user["created_at"],
        },
        "tokens": tokens
    }


@app.patch("/api/auth/password")
async def auth_change_password(body: PasswordChangeRequest, user: dict = Depends(get_current_user)):
    if not verify_password(body.old_password, user["password_hash"]):
        raise HTTPException(400, "Old password is incorrect")
    if len(body.new_password) < 4:
        raise HTTPException(400, "New password must be at least 4 characters")
    update_user_password(user["id"], body.new_password)
    return {"ok": True}


# ── User API ──────────────────────────────────────

@app.post("/api/user/tokens")
async def user_create_token(body: TokenCreateUserRequest, user: dict = Depends(get_current_user)):
    td = create_token(
        name=body.name or f"{user['name']}'s Token",
        email=user["email"],
        role="user",
        quota=-1,  # Use user-level quota
        rate_limit_rpm=30,
        user_id=user["id"]
    )
    return {"ok": True, "token": td}


@app.get("/api/user/tokens")
async def user_list_tokens(user: dict = Depends(get_current_user)):
    tokens = list_tokens(user_id=user["id"])
    return {"tokens": tokens}


@app.delete("/api/user/tokens/{tid}")
async def user_delete_token(tid: int, user: dict = Depends(get_current_user)):
    td = get_token_by_id(tid, user_id=user["id"])
    if not td:
        raise HTTPException(404, "Token not found")
    revoke_token(tid)
    return {"ok": True}


@app.get("/api/user/usage")
async def user_usage(user: dict = Depends(get_current_user)):
    stats = get_usage_stats(user_id=user["id"])
    logs = get_recent_logs(limit=50, user_id=user["id"])
    return {"stats": stats, "logs": logs}


# ── Admin API: Stats ──────────────────────────────

@app.get("/admin/api/stats")
async def api_stats(days: int = 30, admin: dict = Depends(get_admin_user)):
    return get_usage_stats(days)


@app.get("/admin/api/logs")
async def api_logs(limit: int = 100, admin: dict = Depends(get_admin_user)):
    return {"logs": get_recent_logs(limit)}


# ── Admin API: Users ──────────────────────────────

@app.get("/admin/api/users")
async def api_users(admin: dict = Depends(get_admin_user)):
    users = list_users()
    # Attach token count to each user
    for u in users:
        tokens = list_tokens(user_id=u["id"])
        u["token_count"] = len(tokens)
    return {"users": users}


@app.patch("/admin/api/users/{uid}")
async def api_update_user(uid: int, body: UserUpdateRequest, admin: dict = Depends(get_admin_user)):
    update_user(uid, **body.model_dump(exclude_none=True))
    return {"ok": True}


@app.get("/admin/api/users/{uid}/tokens")
async def api_user_tokens(uid: int, admin: dict = Depends(get_admin_user)):
    tokens = list_tokens(user_id=uid)
    return {"tokens": tokens}


@app.get("/admin/api/users/{uid}/usage")
async def api_user_usage(uid: int, admin: dict = Depends(get_admin_user)):
    stats = get_usage_stats(user_id=uid)
    logs = get_recent_logs(limit=50, user_id=uid)
    return {"stats": stats, "logs": logs}


# ── Admin API: Providers ──────────────────────────

@app.get("/admin/api/providers")
async def api_providers(admin: dict = Depends(get_admin_user)):
    providers = get_providers()
    models_list = get_models()
    for p in providers:
        p["channel_count"] = len([c for c in get_channels(p["id"])])
        p["model_count"] = len([m for m in models_list if m["provider_id"] == p["id"]])
    return {"providers": providers}


# ── Admin API: Channels ───────────────────────────

@app.get("/admin/api/channels")
async def api_channels(provider_id: str | None = None, admin: dict = Depends(get_admin_user)):
    return {"channels": get_channels(provider_id)}


@app.post("/admin/api/channels")
async def api_add_channel(body: ChannelRequest, admin: dict = Depends(get_admin_user)):
    add_channel(body.provider_id, body.name, body.api_key, body.weight)
    return {"ok": True}


@app.patch("/admin/api/channels/{cid}")
async def api_update_channel(cid: int, body: ChannelUpdateRequest, admin: dict = Depends(get_admin_user)):
    update_channel(cid, **body.model_dump(exclude_none=True))
    return {"ok": True}


@app.delete("/admin/api/channels/{cid}")
async def api_delete_channel(cid: int, admin: dict = Depends(get_admin_user)):
    delete_channel(cid)
    return {"ok": True}


# ── Admin API: Models ─────────────────────────────

@app.get("/admin/api/models")
async def api_models(provider_id: str | None = None, admin: dict = Depends(get_admin_user)):
    return {"models": get_models(provider_id)}


@app.post("/admin/api/models")
async def api_add_model(body: ModelRequest, admin: dict = Depends(get_admin_user)):
    add_model(body.model_id, body.provider_id, body.display_name, body.input_price, body.output_price, body.unit_size)
    return {"ok": True}


@app.delete("/admin/api/models/{provider_id}/{model_id}")
async def api_delete_model(provider_id: str, model_id: str, admin: dict = Depends(get_admin_user)):
    delete_model(model_id, provider_id)
    return {"ok": True}


# ── Admin API: Tokens (legacy) ────────────────────

@app.get("/admin/api/tokens")
async def api_tokens(admin: dict = Depends(get_admin_user)):
    return {"tokens": list_tokens()}


@app.post("/admin/api/tokens")
async def api_create_token(body: TokenCreateRequest, admin: dict = Depends(get_admin_user)):
    td = create_token(body.name, body.email, body.role, body.quota, body.rate_limit_rpm)
    return {"ok": True, "token": td}


@app.patch("/admin/api/tokens/{tid}")
async def api_update_token(tid: int, body: TokenUpdateRequest, admin: dict = Depends(get_admin_user)):
    update_token(tid, **body.model_dump(exclude_none=True))
    return {"ok": True}


@app.delete("/admin/api/tokens/{tid}")
async def api_revoke_token(tid: int, admin: dict = Depends(get_admin_user)):
    revoke_token(tid)
    return {"ok": True}


@app.post("/admin/api/tokens/{tid}/quota")
async def api_add_quota(tid: int, body: QuotaAddRequest, admin: dict = Depends(get_admin_user)):
    add_quota(tid, body.amount)
    return {"ok": True}


# ── Public API: Self-serve token (now creates user) ─

@app.post("/api/public/register")
async def public_register(request: Request):
    try:
        body_req = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")
    name = body_req.get("name", "").strip()
    email = body_req.get("email", "").strip()
    if not name:
        raise HTTPException(400, "Name is required")
    # Check if user exists, if so just return existing info
    user = get_user_by_email(email) if email else None
    if user:
        tokens = list_tokens(user_id=user["id"])
        if tokens:
            return {"ok": True, "existing_user": True, "message": "Email already registered. Please log in."}
    quota = DEFAULT_QUOTA
    td = create_token(name=name, email=email, role="user", quota=quota, rate_limit_rpm=30)
    return {"ok": True, "token": td["token"], "name": name, "quota": quota}


# ── Public API: Models & Pricing ──────────────────

@app.get("/api/public/models")
async def public_models():
    models = get_models()
    return {
        "models": [
            {
                "id": m["id"],
                "display_name": m["display_name"],
                "provider": m["provider_name"],
                "input_price": m["input_price"],
                "output_price": m["output_price"],
                "unit_size": m["unit_size"],
                "unit_label": f"per {m['unit_size']} tokens"
            }
            for m in models
        ]
    }


# ── Health ────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)

