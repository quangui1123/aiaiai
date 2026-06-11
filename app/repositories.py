import hashlib, secrets, random, bcrypt
from datetime import datetime, timezone
from typing import Optional
from app.database import get_db


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


async def create_user(email: str, password: str, name: str = "", is_admin: bool = False, quota: float = 0.5):
    async with get_db() as conn:
        h = _hash_password(password)
        try:
            await conn.execute("INSERT INTO users (email, password_hash, name, is_admin, quota) VALUES (?,?,?,?,?)",
                              (email.strip().lower(), h, name, 1 if is_admin else 0, quota))
            await conn.commit()
            row = await conn.execute_fetchall("SELECT * FROM users WHERE email = ?", (email.strip().lower(),))
            return dict(row[0]) if row else None
        except Exception:
            return None


async def get_user_by_email(email: str, include_disabled: bool = False):
    async with get_db() as conn:
        email = email.strip().lower()
        if include_disabled:
            rows = await conn.execute_fetchall("SELECT * FROM users WHERE email = ?", (email,))
        else:
            rows = await conn.execute_fetchall("SELECT * FROM users WHERE email = ? AND enabled = 1", (email,))
        return _row_to_dict(rows)


async def get_user_by_id(uid: int):
    async with get_db() as conn:
        rows = await conn.execute_fetchall("SELECT * FROM users WHERE id = ?", (uid,))
        return _row_to_dict(rows)


async def list_users():
    async with get_db() as conn:
        rows = await conn.execute_fetchall(
            "SELECT id, email, name, is_admin, quota, used_quota, rate_limit_rpm, enabled, created_at FROM users ORDER BY id"
        )
        result = []
        for r in rows:
            d = dict(r)
            d["is_admin"] = bool(d["is_admin"])
            d["enabled"] = bool(d["enabled"])
            result.append(d)
        return result


async def update_user(uid: int, **kw) -> bool:
    allowed = {"quota", "rate_limit_rpm", "enabled", "is_admin", "name"}
    updates = {k: v for k, v in kw.items() if k in allowed}
    if "enabled" in updates:
        updates["enabled"] = 1 if updates["enabled"] else 0
    if "is_admin" in updates:
        updates["is_admin"] = 1 if updates["is_admin"] else 0
    if not updates:
        return True
    async with get_db() as conn:
        parts = ", ".join(f"{k}=?" for k in updates)
        await conn.execute(f"UPDATE users SET {parts} WHERE id=?", (*updates.values(), uid))
        await conn.commit()
    return True


async def update_user_password(uid: int, new_password: str) -> bool:
    h = _hash_password(new_password)
    async with get_db() as conn:
        await conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (h, uid))
        await conn.commit()
    return True


async def check_user_quota(user_id: int) -> bool:
    async with get_db() as conn:
        rows = await conn.execute_fetchall("SELECT quota, used_quota FROM users WHERE id = ? AND enabled = 1", (user_id,))
        if not rows:
            return False
        r = rows[0]
        if r["quota"] >= 0 and r["used_quota"] >= r["quota"]:
            return False
    return True


async def create_token(name, email="", role="user", quota=-1, rate_limit_rpm=60, user_id=None):
    token = "sk-" + secrets.token_urlsafe(32)
    h = _hash_token(token)
    prefix = token[:12]
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO user_tokens (user_id, name, email, token_hash, token_prefix, role, quota, rate_limit_rpm) VALUES (?,?,?,?,?,?,?,?)",
            (user_id, name, email, h, prefix, role, quota, rate_limit_rpm))
        await conn.commit()
        rows = await conn.execute_fetchall("SELECT * FROM user_tokens WHERE token_hash = ?", (h,))
        if rows:
            r = dict(rows[0])
            r["token"] = token
            return r
    return None


async def verify_token(token):
    h = _hash_token(token)
    async with get_db() as conn:
        rows = await conn.execute_fetchall("SELECT * FROM user_tokens WHERE token_hash = ? AND enabled = 1", (h,))
        if not rows:
            return None
        d = dict(rows[0])
        if d.get("user_id"):
            urows = await conn.execute_fetchall("SELECT quota, used_quota, enabled FROM users WHERE id = ?", (d["user_id"],))
            if urows:
                u = urows[0]
                if not u["enabled"]:
                    return None
                if u["quota"] >= 0 and u["used_quota"] >= u["quota"]:
                    return None
        else:
            if d["quota"] >= 0 and d["used_quota"] >= d["quota"]:
                return None
    return d


async def list_tokens(user_id=None):
    async with get_db() as conn:
        if user_id:
            rows = await conn.execute_fetchall(
                "SELECT id, user_id, name, email, token_prefix, role, quota, used_quota, rate_limit_rpm, enabled, created_at, last_used_at FROM user_tokens WHERE user_id = ? ORDER BY id",
                (user_id,))
        else:
            rows = await conn.execute_fetchall(
                "SELECT id, user_id, name, email, token_prefix, role, quota, used_quota, rate_limit_rpm, enabled, created_at, last_used_at FROM user_tokens ORDER BY id")
    return [dict(r) for r in rows]


async def get_token_by_id(tid, user_id=None):
    async with get_db() as conn:
        if user_id:
            rows = await conn.execute_fetchall("SELECT * FROM user_tokens WHERE id = ? AND user_id = ?", (tid, user_id))
        else:
            rows = await conn.execute_fetchall("SELECT * FROM user_tokens WHERE id = ?", (tid,))
    return _row_to_dict(rows)


async def revoke_token(tid):
    async with get_db() as conn:
        await conn.execute("UPDATE user_tokens SET enabled = 0 WHERE id = ?", (tid,))
        await conn.commit()


async def update_token(tid, **kw):
    allowed = {"name", "email", "quota", "rate_limit_rpm", "enabled"}
    updates = {k: v for k, v in kw.items() if k in allowed and v is not None}
    if "enabled" in updates:
        updates["enabled"] = 1 if updates["enabled"] else 0
    if not updates:
        return
    async with get_db() as conn:
        parts = ", ".join(f"{k}=?" for k in updates)
        await conn.execute(f"UPDATE user_tokens SET {parts} WHERE id=?", (*updates.values(), tid))
        await conn.commit()


async def add_quota(tid, amount):
    async with get_db() as conn:
        await conn.execute("UPDATE user_tokens SET quota = quota + ? WHERE id = ? AND quota >= 0", (amount, tid))
        await conn.commit()


async def add_user_quota(uid, amount):
    async with get_db() as conn:
        await conn.execute("UPDATE users SET quota = quota + ? WHERE id = ? AND quota >= 0", (amount, uid))
        await conn.commit()


async def check_rate_limit(tid, rpm) -> bool:
    bucket = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    async with get_db() as conn:
        rows = await conn.execute_fetchall("SELECT request_count FROM rate_limits WHERE token_id=? AND minute_bucket=?", (tid, bucket))
        if rows:
            if rows[0][0] >= rpm:
                return False
            await conn.execute("UPDATE rate_limits SET request_count=request_count+1 WHERE token_id=? AND minute_bucket=?", (tid, bucket))
        else:
            await conn.execute("INSERT INTO rate_limits (token_id, minute_bucket, request_count) VALUES (?,?,1)", (tid, bucket))
        await conn.commit()
    return True


async def log_usage(token_id, user_name, provider, model, prompt_tokens, completion_tokens, total_tokens, quota_used, status="success"):
    async with get_db() as conn:
        await conn.execute("INSERT INTO usage_logs (token_id, user_name, provider, model, prompt_tokens, completion_tokens, total_tokens, quota_used, status) VALUES (?,?,?,?,?,?,?,?,?)",
                           (token_id, user_name, provider, model, prompt_tokens, completion_tokens, total_tokens, quota_used, status))
        if quota_used > 0:
            trows = await conn.execute_fetchall("SELECT user_id FROM user_tokens WHERE id = ?", (token_id,))
            if trows and trows[0]["user_id"]:
                await conn.execute("UPDATE users SET used_quota = used_quota + ? WHERE id = ?", (quota_used, trows[0]["user_id"]))
            else:
                await conn.execute("UPDATE user_tokens SET used_quota = used_quota + ? WHERE id = ?", (quota_used, token_id))
        await conn.execute("UPDATE user_tokens SET last_used_at = datetime('now') WHERE id = ?", (token_id,))
        await conn.commit()


async def get_usage_stats(days=30, user_id=None):
    async with get_db() as conn:
        time_filter = f"-{days} days"
        if user_id:
            tids = [r[0] for r in await conn.execute_fetchall("SELECT id FROM user_tokens WHERE user_id = ?", (user_id,))]
            if not tids:
                return {"total_requests": 0, "total_prompt_tokens": 0, "total_completion_tokens": 0, "total_tokens": 0, "total_cost": 0, "by_provider": [], "by_model": [], "by_token": []}
            ph = ",".join("?" * len(tids))
            params = (*tids, time_filter)
            total = (await conn.execute_fetchall(f"SELECT COUNT(*) as n, COALESCE(SUM(prompt_tokens),0) pt, COALESCE(SUM(completion_tokens),0) ct, COALESCE(SUM(total_tokens),0) tt, COALESCE(SUM(quota_used),0) cost FROM usage_logs WHERE token_id IN ({ph}) AND created_at >= datetime('now', ?)", params))[0]
            by_provider = await conn.execute_fetchall(f"SELECT provider, COUNT(*) n, COALESCE(SUM(total_tokens),0) t, COALESCE(SUM(quota_used),0) cost FROM usage_logs WHERE token_id IN ({ph}) AND created_at >= datetime('now', ?) GROUP BY provider ORDER BY n DESC", params)
            by_model = await conn.execute_fetchall(f"SELECT model, COUNT(*) n, COALESCE(SUM(total_tokens),0) t, COALESCE(SUM(quota_used),0) cost FROM usage_logs WHERE token_id IN ({ph}) AND created_at >= datetime('now', ?) GROUP BY model ORDER BY n DESC", params)
            by_token = await conn.execute_fetchall(f"SELECT user_name, COUNT(*) n, COALESCE(SUM(total_tokens),0) t, COALESCE(SUM(quota_used),0) cost FROM usage_logs WHERE token_id IN ({ph}) AND created_at >= datetime('now', ?) GROUP BY token_id ORDER BY n DESC", params)
        else:
            total = (await conn.execute_fetchall("SELECT COUNT(*) as n, COALESCE(SUM(prompt_tokens),0) pt, COALESCE(SUM(completion_tokens),0) ct, COALESCE(SUM(total_tokens),0) tt, COALESCE(SUM(quota_used),0) cost FROM usage_logs WHERE created_at >= datetime('now', ?)", (time_filter,)))[0]
            by_provider = await conn.execute_fetchall("SELECT provider, COUNT(*) n, COALESCE(SUM(total_tokens),0) t, COALESCE(SUM(quota_used),0) cost FROM usage_logs WHERE created_at >= datetime('now', ?) GROUP BY provider ORDER BY n DESC", (time_filter,))
            by_model = await conn.execute_fetchall("SELECT model, COUNT(*) n, COALESCE(SUM(total_tokens),0) t, COALESCE(SUM(quota_used),0) cost FROM usage_logs WHERE created_at >= datetime('now', ?) GROUP BY model ORDER BY n DESC", (time_filter,))
            by_token = await conn.execute_fetchall("SELECT user_name, COUNT(*) n, COALESCE(SUM(total_tokens),0) t, COALESCE(SUM(quota_used),0) cost FROM usage_logs WHERE created_at >= datetime('now', ?) GROUP BY token_id ORDER BY n DESC", (time_filter,))
    return {
        "total_requests": total["n"],
        "total_prompt_tokens": total["pt"],
        "total_completion_tokens": total["ct"],
        "total_tokens": total["tt"],
        "total_cost": round(total["cost"], 4),
        "by_provider": [dict(r) for r in by_provider],
        "by_model": [dict(r) for r in by_model],
        "by_token": [dict(r) for r in by_token],
    }


async def get_recent_logs(limit=100, user_id=None):
    async with get_db() as conn:
        if user_id:
            tids = [r[0] for r in await conn.execute_fetchall("SELECT id FROM user_tokens WHERE user_id = ?", (user_id,))]
            if not tids:
                return []
            ph = ",".join("?" * len(tids))
            rows = await conn.execute_fetchall(f"SELECT * FROM usage_logs WHERE token_id IN ({ph}) ORDER BY id DESC LIMIT ?", (*tids, limit))
        else:
            rows = await conn.execute_fetchall("SELECT * FROM usage_logs ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


async def get_providers():
    async with get_db() as conn:
        rows = await conn.execute_fetchall("SELECT * FROM providers ORDER BY id")
    return [dict(r) for r in rows]


async def get_channels(provider_id=None):
    async with get_db() as conn:
        if provider_id:
            rows = await conn.execute_fetchall(
                "SELECT c.*, p.name as provider_name FROM channels c JOIN providers p ON c.provider_id=p.id WHERE c.provider_id=? ORDER BY c.id", (provider_id,))
        else:
            rows = await conn.execute_fetchall("SELECT c.*, p.name as provider_name FROM channels c JOIN providers p ON c.provider_id=p.id ORDER BY c.id")
    result = []
    for r in rows:
        d = dict(r)
        if len(d["api_key"]) > 8:
            d["api_key"] = d["api_key"][:4] + "****" + d["api_key"][-4:]
        else:
            d["api_key"] = "****"
        result.append(d)
    return result


async def add_channel(provider_id, name, api_key, weight=1):
    async with get_db() as conn:
        await conn.execute("INSERT INTO channels (provider_id, name, api_key, weight) VALUES (?,?,?,?)", (provider_id, name, api_key, weight))
        await conn.commit()


async def delete_channel(cid):
    async with get_db() as conn:
        await conn.execute("DELETE FROM channels WHERE id=?", (cid,))
        await conn.commit()


async def update_channel(cid, **kw):
    allowed = {"name", "api_key", "weight", "enabled"}
    updates = {k: v for k, v in kw.items() if k in allowed and v is not None}
    if "enabled" in updates:
        updates["enabled"] = 1 if updates["enabled"] else 0
    if not updates:
        return
    async with get_db() as conn:
        parts = ", ".join(f"{k}=?" for k in updates)
        await conn.execute(f"UPDATE channels SET {parts} WHERE id=?", (*updates.values(), cid))
        await conn.commit()


async def get_all_channel_keys(provider_id):
    async with get_db() as conn:
        rows = await conn.execute_fetchall("SELECT id, api_key, weight FROM channels WHERE provider_id=? AND enabled=1 ORDER BY weight DESC", (provider_id,))
    return [(r["api_key"], r["id"]) for r in rows]


async def get_configured_provider_ids():
    async with get_db() as conn:
        rows = await conn.execute_fetchall("SELECT DISTINCT provider_id FROM channels WHERE enabled = 1")
    return {r["provider_id"] for r in rows}


async def resolve_model(model_id: str, provider_hint: Optional[str] = None, _seen: Optional[set] = None):
    if _seen is None:
        _seen = set()
    key = (model_id, provider_hint or "")
    if key in _seen:
        return None
    _seen.add(key)

    async with get_db() as conn:
        configured = await get_configured_provider_ids()

        if provider_hint:
            rows = await conn.execute_fetchall(
                """SELECT m.*, p.name as provider_name FROM models m
                   JOIN providers p ON m.provider_id = p.id
                   WHERE m.id = ? AND m.provider_id = ? AND m.enabled = 1 AND p.enabled = 1""",
                (model_id, provider_hint))
            if rows:
                return dict(rows[0])

        rows = await conn.execute_fetchall(
            """SELECT m.*, p.name as provider_name FROM models m
               JOIN providers p ON m.provider_id = p.id
               WHERE m.id = ? AND m.enabled = 1 AND p.enabled = 1
               ORDER BY m.provider_id""",
            (model_id,))

        if rows:
            with_channel = [dict(r) for r in rows if r["provider_id"] in configured]
            if with_channel:
                return with_channel[0]
            return dict(rows[0])

        all_models = await conn.execute_fetchall(
            """SELECT m.id, m.provider_id, p.name as provider_name FROM models m
               JOIN providers p ON m.provider_id = p.id
               WHERE m.enabled = 1 AND p.enabled = 1
               ORDER BY LENGTH(m.id) DESC""")

    for row in all_models:
        if model_id.startswith(row["id"]):
            if configured and row["provider_id"] not in configured:
                continue
            full = await resolve_model(row["id"], row["provider_id"], _seen)
            if full:
                return full
    return None


async def get_models(provider_id=None):
    async with get_db() as conn:
        if provider_id:
            rows = await conn.execute_fetchall(
                "SELECT m.*, p.name as provider_name FROM models m JOIN providers p ON m.provider_id=p.id WHERE m.provider_id=? AND m.enabled=1 ORDER BY m.id", (provider_id,))
        else:
            rows = await conn.execute_fetchall("SELECT m.*, p.name as provider_name FROM models m JOIN providers p ON m.provider_id=p.id WHERE m.enabled=1 ORDER BY m.id")
    return [dict(r) for r in rows]


async def get_model_price(model_id, provider_id=None):
    async with get_db() as conn:
        if provider_id:
            rows = await conn.execute_fetchall("SELECT input_price, output_price, unit_size, provider_id FROM models WHERE id=? AND provider_id=? AND enabled=1", (model_id, provider_id))
        else:
            rows = await conn.execute_fetchall("SELECT input_price, output_price, unit_size, provider_id FROM models WHERE id=? AND enabled=1 LIMIT 1", (model_id,))
    return dict(rows[0]) if rows else None


async def add_model(model_id, provider_id, display_name, input_price, output_price, unit_size=1000):
    async with get_db() as conn:
        await conn.execute("INSERT OR REPLACE INTO models (id, provider_id, display_name, input_price, output_price, unit_size) VALUES (?,?,?,?,?,?)",
                           (model_id, provider_id, display_name, input_price, output_price, unit_size))
        await conn.commit()


async def delete_model(model_id, provider_id):
    async with get_db() as conn:
        await conn.execute("DELETE FROM models WHERE id=? AND provider_id=?", (model_id, provider_id))
        await conn.commit()


def _row_to_dict(rows):
    if rows:
        return dict(rows[0])
    return None
