import sqlite3, os, secrets, hashlib, random, bcrypt
from datetime import datetime, timezone
from typing import Optional

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "proxy.db")
os.makedirs(DB_DIR, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS providers (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, base_url TEXT NOT NULL,
            enabled INTEGER DEFAULT 1, created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
            api_key TEXT NOT NULL, weight INTEGER DEFAULT 1,
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (provider_id) REFERENCES providers(id)
        );
        CREATE TABLE IF NOT EXISTS models (
            id TEXT NOT NULL, provider_id TEXT NOT NULL,
            display_name TEXT,
            input_price REAL NOT NULL DEFAULT 0,
            output_price REAL NOT NULL DEFAULT 0,
            unit_size INTEGER NOT NULL DEFAULT 1000,
            enabled INTEGER DEFAULT 1,
            PRIMARY KEY (id, provider_id),
            FOREIGN KEY (provider_id) REFERENCES providers(id)
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            is_admin INTEGER DEFAULT 0,
            quota REAL NOT NULL DEFAULT 0.5,
            used_quota REAL NOT NULL DEFAULT 0,
            rate_limit_rpm INTEGER DEFAULT 30,
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS user_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT NOT NULL,
            email TEXT DEFAULT '',
            token_hash TEXT NOT NULL UNIQUE,
            token_prefix TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            quota REAL NOT NULL DEFAULT -1,
            used_quota REAL NOT NULL DEFAULT 0,
            rate_limit_rpm INTEGER DEFAULT 60,
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            last_used_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id INTEGER, user_name TEXT,
            provider TEXT, model TEXT,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            quota_used REAL DEFAULT 0,
            status TEXT DEFAULT 'success',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (token_id) REFERENCES user_tokens(id)
        );
        CREATE TABLE IF NOT EXISTS rate_limits (
            token_id INTEGER, minute_bucket TEXT,
            request_count INTEGER DEFAULT 0,
            PRIMARY KEY (token_id, minute_bucket),
            FOREIGN KEY (token_id) REFERENCES user_tokens(id)
        );
    """)
    # Seed providers
    existing = conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0]
    if existing == 0:
        providers = [
            ("openai", "OpenAI", "https://api.openai.com/v1"),
            ("anthropic", "Anthropic Claude", "https://api.anthropic.com/v1"),
            ("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta"),
            ("deepseek", "DeepSeek", "https://api.deepseek.com/v1"),
            ("moonshot", "Moonshot (Kimi)", "https://api.moonshot.cn/v1"),
            ("zhipu", "ZhipuAI (GLM)", "https://open.bigmodel.cn/api/paas/v4"),
            ("qwen", "Qwen (Tongyi)", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            ("siliconflow", "SiliconFlow", "https://api.siliconflow.cn/v1"),
        ]
        conn.executemany(
            "INSERT INTO providers (id, name, base_url) VALUES (?, ?, ?)", providers
        )
        models = [
            ("gpt-4o", "openai", "GPT-4o", 2.5, 10.0),
            ("gpt-4o-mini", "openai", "GPT-4o Mini", 0.15, 0.6),
            ("gpt-4-turbo", "openai", "GPT-4 Turbo", 10.0, 30.0),
            ("claude-4-sonnet", "anthropic", "Claude 4 Sonnet", 3.0, 15.0),
            ("claude-3-5-sonnet", "anthropic", "Claude 3.5 Sonnet", 3.0, 15.0),
            ("claude-3-5-haiku", "anthropic", "Claude 3.5 Haiku", 0.8, 4.0),
            ("gemini-2.5-flash", "gemini", "Gemini 2.5 Flash", 0.15, 0.6),
            ("gemini-2.5-pro", "gemini", "Gemini 2.5 Pro", 1.25, 10.0),
            ("deepseek-chat", "deepseek", "DeepSeek V3", 0.27, 1.1),
            ("deepseek-reasoner", "deepseek", "DeepSeek R1", 0.55, 2.19),
            ("moonshot-v1-8k", "moonshot", "Kimi 8K", 3.0, 3.0),
            ("moonshot-v1-32k", "moonshot", "Kimi 32K", 6.0, 6.0),
            ("moonshot-v1-128k", "moonshot", "Kimi 128K", 12.0, 12.0),
            ("glm-4-flash", "zhipu", "GLM-4 Flash", 0.1, 0.1),
            ("glm-4-plus", "zhipu", "GLM-4 Plus", 7.0, 7.0),
            ("qwen-max", "qwen", "Qwen Max", 2.0, 6.0),
            ("qwen-plus", "qwen", "Qwen Plus", 0.8, 2.0),
            ("qwen-turbo", "qwen", "Qwen Turbo", 0.3, 0.6),
        ]
        conn.executemany(
            "INSERT INTO models (id, provider_id, display_name, input_price, output_price) VALUES (?, ?, ?, ?, ?)",
            models
        )
    conn.commit()
    conn.close()


# ── User management ─────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_user(email: str, password: str, name: str = "", is_admin: bool = False, quota: float = 0.5) -> dict:
    conn = get_db()
    h = hash_password(password)
    try:
        conn.execute(
            "INSERT INTO users (email, password_hash, name, is_admin, quota) VALUES (?,?,?,?,?)",
            (email.strip().lower(), h, name, 1 if is_admin else 0, quota)
        )
        conn.commit()
        uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        conn.close()
        return dict(row)
    except sqlite3.IntegrityError:
        conn.close()
        return None


def get_user_by_email(email: str) -> Optional[dict]:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE email = ? AND enabled = 1",
        (email.strip().lower(),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(uid: int) -> Optional[dict]:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE id = ?",
        (uid,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_users() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT id, email, name, is_admin, quota, used_quota, rate_limit_rpm, enabled, created_at FROM users ORDER BY id"
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["is_admin"] = bool(d["is_admin"])
        d["enabled"] = bool(d["enabled"])
        result.append(d)
    return result


def update_user(uid: int, **kw) -> bool:
    conn = get_db()
    allowed = {"quota", "rate_limit_rpm", "enabled", "is_admin", "name"}
    updates = {k: v for k, v in kw.items() if k in allowed}
    # Convert booleans
    if "enabled" in updates:
        updates["enabled"] = 1 if updates["enabled"] else 0
    if "is_admin" in updates:
        updates["is_admin"] = 1 if updates["is_admin"] else 0
    if updates:
        parts = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE users SET {parts} WHERE id=?", (*updates.values(), uid))
        conn.commit()
    conn.close()
    return True


def update_user_password(uid: int, new_password: str) -> bool:
    conn = get_db()
    h = hash_password(new_password)
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (h, uid))
    conn.commit()
    conn.close()
    return True


def check_user_quota(user_id: int) -> bool:
    """Return True if user still has quota remaining."""
    conn = get_db()
    row = conn.execute("SELECT quota, used_quota FROM users WHERE id = ? AND enabled = 1", (user_id,)).fetchone()
    conn.close()
    if not row:
        return False
    if row["quota"] >= 0 and row["used_quota"] >= row["quota"]:
        return False
    return True


# ── Token management ────────────────────────────────

def hash_token(t):
    return hashlib.sha256(t.encode()).hexdigest()


def create_token(name, email="", role="user", quota=-1, rate_limit_rpm=60, user_id=None):
    conn = get_db()
    token = "sk-" + secrets.token_urlsafe(32)
    h = hash_token(token)
    prefix = token[:12]
    conn.execute(
        "INSERT INTO user_tokens (user_id, name, email, token_hash, token_prefix, role, quota, rate_limit_rpm) VALUES (?,?,?,?,?,?,?,?)",
        (user_id, name, email, h, prefix, role, quota, rate_limit_rpm)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM user_tokens WHERE token_hash = ?", (h,)).fetchone()
    conn.close()
    r = dict(row)
    r["token"] = token
    return r


def verify_token(token):
    conn = get_db()
    h = hash_token(token)
    row = conn.execute(
        "SELECT * FROM user_tokens WHERE token_hash = ? AND enabled = 1", (h,)
    ).fetchone()
    if not row:
        conn.close()
        return None
    d = dict(row)
    # Check user quota if linked to a user
    if d.get("user_id"):
        user_row = conn.execute("SELECT quota, used_quota, enabled FROM users WHERE id = ?", (d["user_id"],)).fetchone()
        if user_row and not user_row["enabled"]:
            conn.close()
            return None
        if user_row and user_row["quota"] >= 0 and user_row["used_quota"] >= user_row["quota"]:
            conn.close()
            return None
    else:
        # Legacy: check token-level quota
        if d["quota"] >= 0 and d["used_quota"] >= d["quota"]:
            conn.close()
            return None
    conn.close()
    return d


def list_tokens(user_id=None):
    conn = get_db()
    if user_id:
        rows = conn.execute(
            "SELECT id, user_id, name, email, token_prefix, role, quota, used_quota, rate_limit_rpm, enabled, created_at, last_used_at FROM user_tokens WHERE user_id = ? ORDER BY id",
            (user_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, user_id, name, email, token_prefix, role, quota, used_quota, rate_limit_rpm, enabled, created_at, last_used_at FROM user_tokens ORDER BY id"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_token_by_id(tid, user_id=None):
    conn = get_db()
    if user_id:
        row = conn.execute(
            "SELECT * FROM user_tokens WHERE id = ? AND user_id = ?", (tid, user_id)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM user_tokens WHERE id = ?", (tid,)
        ).fetchone()
    conn.close()
    return dict(row) if row else None


def revoke_token(tid):
    conn = get_db()
    conn.execute("UPDATE user_tokens SET enabled = 0 WHERE id = ?", (tid,))
    conn.commit()
    conn.close()


def update_token(tid, **kw):
    conn = get_db()
    allowed = {"name", "email", "quota", "rate_limit_rpm", "enabled"}
    updates = {k: v for k, v in kw.items() if k in allowed}
    if updates:
        parts = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE user_tokens SET {parts} WHERE id=?", (*updates.values(), tid))
        conn.commit()
    conn.close()


def add_quota(tid, amount):
    conn = get_db()
    conn.execute("UPDATE user_tokens SET quota = quota + ? WHERE id = ? AND quota >= 0", (amount, tid))
    conn.commit()
    conn.close()


def add_user_quota(uid, amount):
    conn = get_db()
    conn.execute("UPDATE users SET quota = quota + ? WHERE id = ? AND quota >= 0", (amount, uid))
    conn.commit()
    conn.close()


def check_rate_limit(tid, rpm):
    bucket = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    conn = get_db()
    row = conn.execute(
        "SELECT request_count FROM rate_limits WHERE token_id=? AND minute_bucket=?", (tid, bucket)
    ).fetchone()
    if row:
        if row["request_count"] >= rpm:
            conn.close()
            return False
        conn.execute(
            "UPDATE rate_limits SET request_count=request_count+1 WHERE token_id=? AND minute_bucket=?",
            (tid, bucket)
        )
    else:
        conn.execute(
            "INSERT INTO rate_limits (token_id, minute_bucket, request_count) VALUES (?,?,1)",
            (tid, bucket)
        )
    conn.commit()
    conn.close()
    return True


def log_usage(token_id, user_name, provider, model, prompt_tokens, completion_tokens, total_tokens, quota_used, status="success"):
    conn = get_db()
    conn.execute(
        "INSERT INTO usage_logs (token_id, user_name, provider, model, prompt_tokens, completion_tokens, total_tokens, quota_used, status) VALUES (?,?,?,?,?,?,?,?,?)",
        (token_id, user_name, provider, model, prompt_tokens, completion_tokens, total_tokens, quota_used, status)
    )
    if quota_used > 0:
        # Update user quota if token linked to user
        row = conn.execute("SELECT user_id FROM user_tokens WHERE id = ?", (token_id,)).fetchone()
        if row and row["user_id"]:
            conn.execute("UPDATE users SET used_quota = used_quota + ? WHERE id = ?", (quota_used, row["user_id"]))
        else:
            conn.execute("UPDATE user_tokens SET used_quota = used_quota + ? WHERE id = ?", (quota_used, token_id))
        conn.execute("UPDATE user_tokens SET last_used_at = datetime('now') WHERE id = ?", (token_id,))
    else:
        conn.execute("UPDATE user_tokens SET last_used_at = datetime('now') WHERE id = ?", (token_id,))
    conn.commit()
    conn.close()


def get_usage_stats(days=30, user_id=None):
    conn = get_db()
    if user_id:
        # Get token IDs for this user
        tids = [r[0] for r in conn.execute("SELECT id FROM user_tokens WHERE user_id = ?", (user_id,)).fetchall()]
        if not tids:
            conn.close()
            return {"total_requests": 0, "total_prompt_tokens": 0, "total_completion_tokens": 0, "total_tokens": 0, "total_cost": 0, "by_provider": [], "by_model": []}
        placeholders = ",".join("?" * len(tids))
        total = conn.execute(
            f"SELECT COUNT(*) as n, COALESCE(SUM(prompt_tokens),0) pt, COALESCE(SUM(completion_tokens),0) ct, COALESCE(SUM(total_tokens),0) tt, COALESCE(SUM(quota_used),0) cost FROM usage_logs WHERE token_id IN ({placeholders}) AND created_at >= datetime('now', ?)",
            (*tids, f"-{days} days")
        ).fetchone()
        by_provider = conn.execute(
            f"SELECT provider, COUNT(*) n, COALESCE(SUM(total_tokens),0) t, COALESCE(SUM(quota_used),0) cost FROM usage_logs WHERE token_id IN ({placeholders}) AND created_at >= datetime('now', ?) GROUP BY provider ORDER BY n DESC",
            (*tids, f"-{days} days")
        ).fetchall()
    else:
        total = conn.execute(
            "SELECT COUNT(*) as n, COALESCE(SUM(prompt_tokens),0) pt, COALESCE(SUM(completion_tokens),0) ct, COALESCE(SUM(total_tokens),0) tt, COALESCE(SUM(quota_used),0) cost FROM usage_logs WHERE created_at >= datetime('now', ?)",
            (f"-{days} days",)
        ).fetchone()
        by_provider = conn.execute(
            "SELECT provider, COUNT(*) n, COALESCE(SUM(total_tokens),0) t, COALESCE(SUM(quota_used),0) cost FROM usage_logs WHERE created_at >= datetime('now', ?) GROUP BY provider ORDER BY n DESC",
            (f"-{days} days",)
        ).fetchall()
    by_token = conn.execute(
        "SELECT user_name, COUNT(*) n, COALESCE(SUM(total_tokens),0) t, COALESCE(SUM(quota_used),0) cost FROM usage_logs WHERE created_at >= datetime('now', ?) GROUP BY token_id ORDER BY n DESC",
        (f"-{days} days",)
    ).fetchall()
    conn.close()
    return {
        "total_requests": total["n"],
        "total_prompt_tokens": total["pt"],
        "total_completion_tokens": total["ct"],
        "total_tokens": total["tt"],
        "total_cost": round(total["cost"], 4),
        "by_provider": [dict(r) for r in by_provider],
        "by_token": [dict(r) for r in by_token],
    }


def get_recent_logs(limit=100, user_id=None):
    conn = get_db()
    if user_id:
        tids = [r[0] for r in conn.execute("SELECT id FROM user_tokens WHERE user_id = ?", (user_id,)).fetchall()]
        if not tids:
            conn.close()
            return []
        placeholders = ",".join("?" * len(tids))
        rows = conn.execute(
            f"SELECT * FROM usage_logs WHERE token_id IN ({placeholders}) ORDER BY id DESC LIMIT ?",
            (*tids, limit)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM usage_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Providers / Channels / Models ───────────────────

def get_providers():
    conn = get_db()
    rows = conn.execute("SELECT * FROM providers ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_channels(provider_id=None):
    conn = get_db()
    if provider_id:
        rows = conn.execute(
            "SELECT c.*, p.name as provider_name FROM channels c JOIN providers p ON c.provider_id=p.id WHERE c.provider_id=? ORDER BY c.id",
            (provider_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT c.*, p.name as provider_name FROM channels c JOIN providers p ON c.provider_id=p.id ORDER BY c.id"
        ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        if len(d["api_key"]) > 8:
            d["api_key"] = d["api_key"][:4] + "****" + d["api_key"][-4:]
        else:
            d["api_key"] = "****"
        result.append(d)
    return result


def add_channel(provider_id, name, api_key, weight=1):
    conn = get_db()
    conn.execute(
        "INSERT INTO channels (provider_id, name, api_key, weight) VALUES (?,?,?,?)",
        (provider_id, name, api_key, weight)
    )
    conn.commit()
    conn.close()


def delete_channel(cid):
    conn = get_db()
    conn.execute("DELETE FROM channels WHERE id=?", (cid,))
    conn.commit()
    conn.close()


def update_channel(cid, **kw):
    conn = get_db()
    allowed = {"name", "api_key", "weight", "enabled"}
    updates = {k: v for k, v in kw.items() if k in allowed}
    if updates:
        parts = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE channels SET {parts} WHERE id=?", (*updates.values(), cid))
        conn.commit()
    conn.close()


def get_channel_key(provider_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT id, api_key, weight FROM channels WHERE provider_id=? AND enabled=1", (provider_id,)
    ).fetchall()
    conn.close()
    if not rows:
        return None, None
    if len(rows) == 1:
        return rows[0]["api_key"], rows[0]["id"]
    weights = [r["weight"] for r in rows]
    total = sum(weights)
    r = random.uniform(0, total)
    acc = 0
    for row in rows:
        acc += row["weight"]
        if r <= acc:
            return row["api_key"], row["id"]
    return rows[-1]["api_key"], rows[-1]["id"]


def get_models(provider_id=None):
    conn = get_db()
    if provider_id:
        rows = conn.execute(
            "SELECT m.*, p.name as provider_name FROM models m JOIN providers p ON m.provider_id=p.id WHERE m.provider_id=? AND m.enabled=1 ORDER BY m.id",
            (provider_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT m.*, p.name as provider_name FROM models m JOIN providers p ON m.provider_id=p.id WHERE m.enabled=1 ORDER BY m.id"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_model_price(model_id):
    conn = get_db()
    row = conn.execute(
        "SELECT input_price, output_price, unit_size, provider_id FROM models WHERE id=? AND enabled=1 LIMIT 1",
        (model_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def add_model(model_id, provider_id, display_name, input_price, output_price, unit_size=1000):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO models (id, provider_id, display_name, input_price, output_price, unit_size) VALUES (?,?,?,?,?,?)",
        (model_id, provider_id, display_name, input_price, output_price, unit_size)
    )
    conn.commit()
    conn.close()


def delete_model(model_id, provider_id):
    conn = get_db()
    conn.execute("DELETE FROM models WHERE id=? AND provider_id=?", (model_id, provider_id))
    conn.commit()
    conn.close()
