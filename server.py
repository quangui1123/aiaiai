import os
import time
import httpx
import jwt
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from database import (
    init_db, create_token, verify_token, list_tokens, revoke_token, update_token, add_quota,
    check_rate_limit, log_usage, get_usage_stats, get_recent_logs,
    get_providers, get_channels, add_channel, delete_channel, update_channel,
    get_models, get_model_price, add_model, delete_model,
    create_user, get_user_by_email, get_user_by_id, list_users, update_user, update_user_password,
    get_token_by_id, verify_password, add_user_quota, resolve_model, get_configured_provider_ids,
    get_all_channel_keys,
)
from models import (
    ChatRequest, ModelList, ModelItem,
    TokenCreateRequest, TokenUpdateRequest, QuotaAddRequest,
    ChannelRequest, ChannelUpdateRequest, ModelRequest,
    RegisterRequest, LoginRequest, PasswordChangeRequest,
    TokenCreateUserRequest, UserUpdateRequest,
)
from providers import create_adapter

load_dotenv()
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@aiaiai.com").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
JWT_SECRET = os.getenv("JWT_SECRET", "")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
DEFAULT_QUOTA = float(os.getenv("DEFAULT_QUOTA", "0.5"))
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")

import secrets as _secrets
if not JWT_SECRET:
    JWT_SECRET = _secrets.token_urlsafe(32)

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 168


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


def openai_error(status: int, message: str, err_type: str = "invalid_request_error"):
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": err_type, "code": None}},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    for _ in range(5):
        if get_user_by_email(ADMIN_EMAIL, include_disabled=True):
            break
        try:
            create_user(
                email=ADMIN_EMAIL,
                password=ADMIN_PASSWORD,
                name="Admin",
                is_admin=True,
                quota=-1,
            )
            break
        except Exception:
            time.sleep(0.5)
    yield


app = FastAPI(title="AI Token Proxy", version="2.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if CORS_ORIGINS == "*" else [o.strip() for o in CORS_ORIGINS.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer(auto_error=False)
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def extract_bearer_token(authorization: str | None, credentials: HTTPAuthorizationCredentials | None) -> str | None:
    if credentials and credentials.credentials:
        return credentials.credentials
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        return authorization.strip()
    return None


async def get_token_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    token = extract_bearer_token(request.headers.get("authorization"), credentials)
    if not token:
        raise HTTPException(401, detail={"error": {"message": "Missing authorization header", "type": "invalid_request_error"}})
    td = verify_token(token)
    if not td:
        raise HTTPException(401, detail={"error": {"message": "Invalid or revoked token, or quota exceeded", "type": "invalid_request_error"}})
    return td


async def get_current_user(authorization: str | None = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing authorization header")
    payload = decode_jwt(authorization.split(" ", 1)[1])
    if not payload:
        raise HTTPException(401, "Invalid or expired token")
    user = get_user_by_id(int(payload["sub"]))
    if not user or not user["enabled"]:
        raise HTTPException(401, "User not found or disabled")
    return user


async def get_admin_user(user: dict = Depends(get_current_user)):
    if not user.get("is_admin"):
        raise HTTPException(403, "Admin access required")
    return user


def calc_cost(model_id, provider_id, prompt_tokens, completion_tokens):
    price = get_model_price(model_id, provider_id)
    if not price:
        return 0
    units = price["unit_size"]
    in_cost = (prompt_tokens / units) * price["input_price"]
    out_cost = (completion_tokens / units) * price["output_price"]
    return round(in_cost + out_cost, 6)


def effective_rpm(td: dict) -> int:
    if td.get("user_id"):
        user = get_user_by_id(td["user_id"])
        if user and user.get("rate_limit_rpm") is not None:
            return user["rate_limit_rpm"]
    rpm = td.get("rate_limit_rpm")
    return rpm if rpm is not None else 60


def read_html(name: str) -> HTMLResponse:
    path = os.path.join(STATIC_DIR, name)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse(f"<h1>{name} not found</h1>", 404)


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 3)


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest, td: dict = Depends(get_token_user)):
    tid = td["id"]
    uname = td["name"]
    rpm = effective_rpm(td)

    if not check_rate_limit(tid, rpm):
        return openai_error(429, "Rate limit exceeded", "rate_limit_error")

    model_info = resolve_model(req.model, req.provider)
    if not model_info:
        return openai_error(400, f"Unknown model: {req.model}")

    provider_id = model_info["provider_id"]
    actual_model = model_info["id"]

    channels = get_all_channel_keys(provider_id)
    if not channels:
        return openai_error(503, f"No API key configured for provider '{provider_id}'. Add a channel in admin panel.")

    providers = get_providers()
    pinfo = next((p for p in providers if p["id"] == provider_id and p["enabled"]), None)
    if not pinfo:
        return openai_error(503, f"Provider '{provider_id}' is disabled or not found")

    req_copy = req.model_copy(update={"model": actual_model})
    prompt_est = sum(estimate_tokens(m.content) for m in req.messages)
    last_error = None

    for api_key, _channel_id in channels:
        adapter = None
        try:
            adapter = await create_adapter(provider_id, api_key, pinfo["base_url"])

            if req.stream:
                async def generate_fixed(ad=adapter):
                    pt, ct, tt, content_len = 0, 0, 0, 0
                    try:
                        async for chunk in ad.chat_stream(req_copy):
                            if chunk.startswith("data: "):
                                payload = chunk[6:].strip()
                                if payload != "[DONE]":
                                    try:
                                        import json as _json
                                        data = _json.loads(payload)
                                        usage = data.get("usage")
                                        if usage:
                                            pt = usage.get("prompt_tokens", pt)
                                            ct = usage.get("completion_tokens", ct)
                                            tt = usage.get("total_tokens", tt)
                                        delta = (data.get("choices") or [{}])[0].get("delta", {})
                                        if delta.get("content"):
                                            content_len += len(delta["content"])
                                    except Exception:
                                        pass
                            yield chunk
                        if pt == 0:
                            pt = prompt_est
                        if ct == 0 and content_len:
                            ct = estimate_tokens("x" * content_len)
                        cost = calc_cost(actual_model, provider_id, pt, ct)
                        log_usage(tid, uname, provider_id, actual_model, pt, ct, tt or (pt + ct), cost, "success")
                    except Exception:
                        log_usage(tid, uname, provider_id, actual_model, 0, 0, 0, 0, "error")
                        raise
                    finally:
                        await ad.close()

                return StreamingResponse(
                    generate_fixed(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                )

            resp = await adapter.chat(req_copy)
            cost = calc_cost(actual_model, provider_id, resp.usage.prompt_tokens, resp.usage.completion_tokens)
            log_usage(
                tid, uname, provider_id, actual_model,
                resp.usage.prompt_tokens, resp.usage.completion_tokens,
                resp.usage.total_tokens, cost, "success",
            )
            await adapter.close()
            return resp

        except httpx.HTTPStatusError as e:
            last_error = e
            if adapter:
                await adapter.close()
            if e.response.status_code in (401, 403, 429):
                continue
            detail = ""
            try:
                detail = e.response.json()
                if isinstance(detail, dict) and "error" in detail:
                    return JSONResponse(status_code=e.response.status_code, content=detail)
            except Exception:
                detail = e.response.text[:500] if e.response.text else str(e)
            return openai_error(502, f"Upstream {e.response.status_code}: {detail}", "upstream_error")
        except Exception as e:
            last_error = e
            if adapter:
                await adapter.close()
            continue

    log_usage(tid, uname, provider_id, actual_model, 0, 0, 0, 0, "error")
    msg = str(last_error) if last_error else "All channels failed"
    return openai_error(502, msg, "server_error")


@app.get("/v1/models")
async def list_models_api(td: dict = Depends(get_token_user)):
    configured = get_configured_provider_ids()
    models = get_models()
    if configured:
        models = [m for m in models if m["provider_id"] in configured]
    data = [
        ModelItem(id=m["id"], created=int(time.time()), owned_by=m["provider_name"])
        for m in models
    ]
    return ModelList(data=data)


@app.get("/admin")
async def admin_panel():
    return read_html("admin.html")


@app.get("/pricing")
async def pricing_page():
    return read_html("pricing.html")


@app.get("/login")
async def login_page():
    return read_html("login.html")


@app.get("/dashboard")
async def dashboard_page():
    return read_html("dashboard.html")


@app.get("/")
async def index():
    return read_html("index.html")


@app.post("/api/auth/register")
async def auth_register(body: RegisterRequest):
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Invalid email")
    if len(body.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    if get_user_by_email(email, include_disabled=True):
        raise HTTPException(400, "Email already registered")

    user = create_user(
        email=email,
        password=body.password,
        name=body.name or email.split("@")[0],
        is_admin=False,
        quota=DEFAULT_QUOTA,
    )
    if not user:
        raise HTTPException(400, "Registration failed")

    td = create_token(
        name=f"{user['name']}'s Token",
        email=email,
        role="user",
        quota=-1,
        rate_limit_rpm=30,
        user_id=user["id"],
    )
    jwt_token = create_jwt(user["id"], user["email"], bool(user["is_admin"]))
    return {
        "ok": True,
        "token": jwt_token,
        "user": _user_payload(user),
        "api_token": td["token"],
    }


@app.post("/api/auth/login")
async def auth_login(body: LoginRequest):
    email = body.email.strip().lower()
    user = get_user_by_email(email, include_disabled=True)
    if not user:
        raise HTTPException(401, "Invalid email or password")
    if not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Invalid email or password")
    if not user["enabled"]:
        raise HTTPException(403, "Account disabled")

    jwt_token = create_jwt(user["id"], user["email"], bool(user["is_admin"]))
    return {"ok": True, "token": jwt_token, "user": _user_payload(user)}


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


@app.get("/api/auth/me")
async def auth_me(user: dict = Depends(get_current_user)):
    tokens = list_tokens(user_id=user["id"])
    return {"user": {**_user_payload(user), "enabled": bool(user["enabled"]), "created_at": user["created_at"]}, "tokens": tokens}


@app.patch("/api/auth/password")
async def auth_change_password(body: PasswordChangeRequest, user: dict = Depends(get_current_user)):
    if not verify_password(body.old_password, user["password_hash"]):
        raise HTTPException(400, "Old password is incorrect")
    if len(body.new_password) < 6:
        raise HTTPException(400, "New password must be at least 6 characters")
    update_user_password(user["id"], body.new_password)
    return {"ok": True}


@app.post("/api/user/tokens")
async def user_create_token(body: TokenCreateUserRequest, user: dict = Depends(get_current_user)):
    td = create_token(
        name=body.name or f"{user['name']}'s Token",
        email=user["email"],
        role="user",
        quota=-1,
        rate_limit_rpm=user.get("rate_limit_rpm") or 30,
        user_id=user["id"],
    )
    return {"ok": True, "token": td}


@app.get("/api/user/tokens")
async def user_list_tokens(user: dict = Depends(get_current_user)):
    return {"tokens": list_tokens(user_id=user["id"])}


@app.delete("/api/user/tokens/{tid}")
async def user_delete_token(tid: int, user: dict = Depends(get_current_user)):
    if not get_token_by_id(tid, user_id=user["id"]):
        raise HTTPException(404, "Token not found")
    revoke_token(tid)
    return {"ok": True}


@app.get("/api/user/usage")
async def user_usage(user: dict = Depends(get_current_user)):
    return {"stats": get_usage_stats(user_id=user["id"]), "logs": get_recent_logs(limit=50, user_id=user["id"])}


@app.get("/admin/api/stats")
async def api_stats(days: int = 30, admin: dict = Depends(get_admin_user)):
    return get_usage_stats(days)


@app.get("/admin/api/logs")
async def api_logs(limit: int = 100, admin: dict = Depends(get_admin_user)):
    return {"logs": get_recent_logs(limit)}


@app.get("/admin/api/users")
async def api_users(admin: dict = Depends(get_admin_user)):
    users = list_users()
    for u in users:
        u["token_count"] = len(list_tokens(user_id=u["id"]))
    return {"users": users}


@app.patch("/admin/api/users/{uid}")
async def api_update_user(uid: int, body: UserUpdateRequest, admin: dict = Depends(get_admin_user)):
    update_user(uid, **body.model_dump(exclude_none=True))
    return {"ok": True}


@app.post("/admin/api/users/{uid}/quota")
async def api_add_user_quota(uid: int, body: QuotaAddRequest, admin: dict = Depends(get_admin_user)):
    add_user_quota(uid, body.amount)
    return {"ok": True}


@app.get("/admin/api/users/{uid}/tokens")
async def api_user_tokens(uid: int, admin: dict = Depends(get_admin_user)):
    return {"tokens": list_tokens(user_id=uid)}


@app.get("/admin/api/users/{uid}/usage")
async def api_user_usage(uid: int, admin: dict = Depends(get_admin_user)):
    return {"stats": get_usage_stats(user_id=uid), "logs": get_recent_logs(limit=50, user_id=uid)}


@app.get("/admin/api/providers")
async def api_providers(admin: dict = Depends(get_admin_user)):
    providers = get_providers()
    models_list = get_models()
    configured = get_configured_provider_ids()
    for p in providers:
        p["channel_count"] = len([c for c in get_channels(p["id"]) if c.get("enabled", 1)])
        p["model_count"] = len([m for m in models_list if m["provider_id"] == p["id"]])
        p["configured"] = p["id"] in configured
    return {"providers": providers}


@app.get("/admin/api/channels")
async def api_channels(provider_id: str | None = None, admin: dict = Depends(get_admin_user)):
    return {"channels": get_channels(provider_id)}


@app.post("/admin/api/channels")
async def api_add_channel(body: ChannelRequest, admin: dict = Depends(get_admin_user)):
    if not body.api_key.strip():
        raise HTTPException(400, "API key is required")
    add_channel(body.provider_id, body.name, body.api_key.strip(), body.weight)
    return {"ok": True}


@app.patch("/admin/api/channels/{cid}")
async def api_update_channel(cid: int, body: ChannelUpdateRequest, admin: dict = Depends(get_admin_user)):
    update_channel(cid, **body.model_dump(exclude_none=True))
    return {"ok": True}


@app.delete("/admin/api/channels/{cid}")
async def api_delete_channel(cid: int, admin: dict = Depends(get_admin_user)):
    delete_channel(cid)
    return {"ok": True}


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


@app.get("/api/public/models")
async def public_models():
    configured = get_configured_provider_ids()
    models = get_models()
    if configured:
        models = [m for m in models if m["provider_id"] in configured]
    return {
        "models": [
            {
                "id": m["id"],
                "display_name": m["display_name"],
                "provider": m["provider_name"],
                "provider_id": m["provider_id"],
                "input_price": m["input_price"],
                "output_price": m["output_price"],
                "unit_size": m["unit_size"],
                "unit_label": f"per {m['unit_size']} tokens",
            }
            for m in models
        ]
    }


@app.get("/api/public/status")
async def public_status():
    configured = get_configured_provider_ids()
    return {
        "status": "ok",
        "providers_configured": len(configured),
        "models_available": len([m for m in get_models() if m["provider_id"] in configured]) if configured else len(get_models()),
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
