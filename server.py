import os, uuid, time, httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from database import (
    init_db, create_token, verify_token, list_tokens, revoke_token, update_token, add_quota,
    check_rate_limit, log_usage, get_usage_stats, get_recent_logs,
    get_providers, get_channels, add_channel, delete_channel, update_channel,
    get_channel_key, get_models, get_model_price, add_model, delete_model
)
from models import (
    ChatRequest, ChatResponse, ModelList, ModelItem,
    TokenCreateRequest, TokenUpdateRequest, QuotaAddRequest,
    ChannelRequest, ChannelUpdateRequest, ModelRequest
)
from providers import create_adapter

load_dotenv()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AI Token Proxy", version="2.0.0", lifespan=lifespan)
security = HTTPBearer(auto_error=False)
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── Auth ──────────────────────────────────────────

async def get_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)):
    if not credentials:
        raise HTTPException(401, "Missing authorization header")
    td = verify_token(credentials.credentials)
    if not td:
        raise HTTPException(401, "Invalid or revoked token, or quota exceeded")
    return td


def admin_required(x_admin_key: str | None = Header(None)):
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
async def chat_completions(req: ChatRequest, td: dict = Depends(get_user)):
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
async def list_models(td: dict = Depends(get_user)):
    models = get_models()
    data = [
        ModelItem(id=m["id"], created=int(time.time()), owned_by=m["provider_name"])
        for m in models
    ]
    return ModelList(data=data)


# ── Admin: Pages ──────────────────────────────────

@app.get("/admin")
async def admin_panel():
    p = os.path.join(STATIC_DIR, "admin.html")
    if not os.path.exists(p):
        return HTMLResponse("<h1>Admin panel not found</h1>", 404)
    return HTMLResponse(open(p, encoding="utf-8").read())


@app.get("/pricing")
async def pricing_page():
    p = os.path.join(STATIC_DIR, "pricing.html")
    if not os.path.exists(p):
        return HTMLResponse("<h1>Pricing page not found</h1>", 404)
    return HTMLResponse(open(p, encoding="utf-8").read())


@app.get("/")
async def index():
    p = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(p):
        return HTMLResponse("<h1>Index not found</h1>", 404)
    return HTMLResponse(open(p, encoding="utf-8").read())


# ── Admin API: Stats ──────────────────────────────

@app.get("/admin/api/stats")
async def api_stats(days: int = 30, _: bool = Depends(admin_required)):
    return get_usage_stats(days)


@app.get("/admin/api/logs")
async def api_logs(limit: int = 100, _: bool = Depends(admin_required)):
    return {"logs": get_recent_logs(limit)}


# ── Admin API: Providers ──────────────────────────

@app.get("/admin/api/providers")
async def api_providers(_: bool = Depends(admin_required)):
    providers = get_providers()
    models_list = get_models()
    for p in providers:
        p["channel_count"] = len([c for c in get_channels(p["id"])])
        p["model_count"] = len([m for m in models_list if m["provider_id"] == p["id"]])
    return {"providers": providers}


# ── Admin API: Channels ───────────────────────────

@app.get("/admin/api/channels")
async def api_channels(provider_id: str | None = None, _: bool = Depends(admin_required)):
    return {"channels": get_channels(provider_id)}


@app.post("/admin/api/channels")
async def api_add_channel(body: ChannelRequest, _: bool = Depends(admin_required)):
    add_channel(body.provider_id, body.name, body.api_key, body.weight)
    return {"ok": True}


@app.patch("/admin/api/channels/{cid}")
async def api_update_channel(cid: int, body: ChannelUpdateRequest, _: bool = Depends(admin_required)):
    update_channel(cid, **body.model_dump(exclude_none=True))
    return {"ok": True}


@app.delete("/admin/api/channels/{cid}")
async def api_delete_channel(cid: int, _: bool = Depends(admin_required)):
    delete_channel(cid)
    return {"ok": True}


# ── Admin API: Models ─────────────────────────────

@app.get("/admin/api/models")
async def api_models(provider_id: str | None = None, _: bool = Depends(admin_required)):
    return {"models": get_models(provider_id)}


@app.post("/admin/api/models")
async def api_add_model(body: ModelRequest, _: bool = Depends(admin_required)):
    add_model(body.model_id, body.provider_id, body.display_name, body.input_price, body.output_price, body.unit_size)
    return {"ok": True}


@app.delete("/admin/api/models/{provider_id}/{model_id}")
async def api_delete_model(provider_id: str, model_id: str, _: bool = Depends(admin_required)):
    delete_model(model_id, provider_id)
    return {"ok": True}


# ── Admin API: Tokens ─────────────────────────────

@app.get("/admin/api/tokens")
async def api_tokens(_: bool = Depends(admin_required)):
    return {"tokens": list_tokens()}


@app.post("/admin/api/tokens")
async def api_create_token(body: TokenCreateRequest, _: bool = Depends(admin_required)):
    td = create_token(body.name, body.email, body.role, body.quota, body.rate_limit_rpm)
    return {"ok": True, "token": td}


@app.patch("/admin/api/tokens/{tid}")
async def api_update_token(tid: int, body: TokenUpdateRequest, _: bool = Depends(admin_required)):
    update_token(tid, **body.model_dump(exclude_none=True))
    return {"ok": True}


@app.delete("/admin/api/tokens/{tid}")
async def api_revoke_token(tid: int, _: bool = Depends(admin_required)):
    revoke_token(tid)
    return {"ok": True}


@app.post("/admin/api/tokens/{tid}/quota")
async def api_add_quota(tid: int, body: QuotaAddRequest, _: bool = Depends(admin_required)):
    add_quota(tid, body.amount)
    return {"ok": True}


# ── Public API: Self-serve token ──────────────────

@app.post("/api/public/register")
async def public_register(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")
    name = body.get("name", "").strip()
    email = body.get("email", "").strip()
    if not name:
        raise HTTPException(400, "Name is required")
    quota = float(os.getenv("DEFAULT_QUOTA", "0.5"))
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
