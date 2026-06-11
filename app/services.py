import time, json, logging
from datetime import datetime, timedelta, timezone
import httpx
import jwt
from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import settings
from app.models import ChatRequest, ChatResponse, ModelItem, ModelList, Usage, Message, ChatChoice
from app import repositories as repo
from app.adapters import create_adapter

logger = logging.getLogger("ai-proxy")


def create_jwt(user_id: int, email: str, is_admin: bool) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "is_admin": is_admin,
        "exp": datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.effective_jwt_secret, algorithm=settings.jwt_algorithm)


def decode_jwt(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.effective_jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None


def openai_error(status: int, message: str, err_type: str = "invalid_request_error"):
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": err_type, "code": None}},
    )


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 3)


def calc_cost(model_id, provider_id, prompt_tokens, completion_tokens):
    import asyncio
    price = asyncio.run(repo.get_model_price(model_id, provider_id))
    if not price:
        return 0
    units = price["unit_size"]
    in_cost = (prompt_tokens / units) * price["input_price"]
    out_cost = (completion_tokens / units) * price["output_price"]
    return round(in_cost + out_cost, 6)


async def get_effective_rpm(td: dict) -> int:
    if td.get("user_id"):
        user = await repo.get_user_by_id(td["user_id"])
        if user and user.get("rate_limit_rpm") is not None:
            return user["rate_limit_rpm"]
    rpm = td.get("rate_limit_rpm")
    return rpm if rpm is not None else 60


async def handle_chat_completion(req: ChatRequest, td: dict):
    tid = td["id"]
    uname = td["name"]
    rpm = await get_effective_rpm(td)

    if not await repo.check_rate_limit(tid, rpm):
        return openai_error(429, "Rate limit exceeded", "rate_limit_error")

    model_info = await repo.resolve_model(req.model, req.provider)
    if not model_info:
        return openai_error(400, f"Unknown model: {req.model}")

    provider_id = model_info["provider_id"]
    actual_model = model_info["id"]

    channels = await repo.get_all_channel_keys(provider_id)
    if not channels:
        return openai_error(503, f"No API key configured for provider '{provider_id}'. Add a channel in admin panel.")

    providers = await repo.get_providers()
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
                return await _handle_stream(adapter, req_copy, tid, uname, provider_id, actual_model, prompt_est)

            resp = await adapter.chat(req_copy)
            cost = calc_cost(actual_model, provider_id, resp.usage.prompt_tokens, resp.usage.completion_tokens)
            await repo.log_usage(tid, uname, provider_id, actual_model,
                                 resp.usage.prompt_tokens, resp.usage.completion_tokens,
                                 resp.usage.total_tokens, cost, "success")
            await adapter.close()
            return resp

        except httpx.HTTPStatusError as e:
            last_error = e
            if adapter:
                await adapter.close()
            if e.response.status_code in (401, 403, 429):
                continue
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

    await repo.log_usage(tid, uname, provider_id, actual_model, 0, 0, 0, 0, "error")
    msg = str(last_error) if last_error else "All channels failed"
    return openai_error(502, msg, "server_error")


async def _handle_stream(adapter, req_copy, tid, uname, provider_id, actual_model, prompt_est):
    async def generate_fixed(ad=adapter):
        pt, ct, tt, content_len = 0, 0, 0, 0
        try:
            async for chunk in ad.chat_stream(req_copy):
                if chunk.startswith("data: "):
                    payload = chunk[6:].strip()
                    if payload != "[DONE]":
                        try:
                            data = json.loads(payload)
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
            await repo.log_usage(tid, uname, provider_id, actual_model, pt, ct, tt or (pt + ct), cost, "success")
        except Exception:
            await repo.log_usage(tid, uname, provider_id, actual_model, 0, 0, 0, 0, "error")
            raise
        finally:
            await ad.close()

    return StreamingResponse(
        generate_fixed(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


async def handle_list_models(td: dict):
    configured = await repo.get_configured_provider_ids()
    models = await repo.get_models()
    if configured:
        models = [m for m in models if m["provider_id"] in configured]
    data = [ModelItem(id=m["id"], created=int(time.time()), owned_by=m["provider_name"]) for m in models]
    return ModelList(data=data)


async def handle_public_models():
    configured = await repo.get_configured_provider_ids()
    models = await repo.get_models()
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
            } for m in models
        ]
    }


async def handle_public_status():
    configured = await repo.get_configured_provider_ids()
    all_models = await repo.get_models()
    if configured:
        available = len([m for m in all_models if m["provider_id"] in configured])
    else:
        available = len(all_models)
    return {"status": "ok", "providers_configured": len(configured), "models_available": available}


def read_html(name: str):
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", name)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            from fastapi.responses import HTMLResponse
            return HTMLResponse(f.read())
    from fastapi.responses import HTMLResponse
    return HTMLResponse(f"<h1>{name} not found</h1>", 404)
