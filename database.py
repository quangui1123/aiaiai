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
            ("moonshot", "Moonshot Kimi", "https://api.moonshot.cn/v1"),
            ("zhipu", "ZhipuAI GLM", "https://open.bigmodel.cn/api/paas/v4"),
            ("qwen", "Qwen Tongyi", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            ("siliconflow", "SiliconFlow", "https://api.siliconflow.cn/v1"),
            ("doubao", "Doubao", "https://ark.cn-beijing.volces.com/api/v3"),
            ("baidu", "Baidu ERNIE", "https://qianfan.baidubce.com/v2"),
            ("tencent", "Tencent Hunyuan", "https://api.hunyuan.cloud.tencent.com/v1"),
            ("minimax", "MiniMax", "https://api.minimax.chat/v1"),
            ("lingyi", "01.AI Yi", "https://api.lingyiwanwu.com/v1"),
            ("mistral", "Mistral AI", "https://api.mistral.ai/v1"),
            ("cohere", "Cohere", "https://api.cohere.ai/v1"),
            ("xai", "xAI Grok", "https://api.x.ai/v1"),
            ("perplexity", "Perplexity", "https://api.perplexity.ai"),
            ("openrouter", "OpenRouter", "https://openrouter.ai/api/v1"),
            ("xunfei", "iFlytek Spark", "https://spark-api-open.xf-yun.com/v1"),
            ("360ai", "360 AI", "https://api.360ai.cn/v1"),
            ("stepfun", "StepFun", "https://api.stepfun.com/v1"),
            ("baichuan", "Baichuan AI", "https://api.baichuan-ai.com/v1"),
            ("groq", "Groq", "https://api.groq.com/openai/v1"),
            ("together", "Together AI", "https://api.together.xyz/v1"),
            ("fireworks", "Fireworks AI", "https://api.fireworks.ai/inference/v1"),
            ("jina", "Jina AI", "https://api.jina.ai/v1"),
            ("voyage", "Voyage AI", "https://api.voyageai.com/v1"),
        ]
        conn.executemany(
            "INSERT INTO providers (id, name, base_url) VALUES (?, ?, ?)", providers
        )
        models = [
            ("gpt-4o", "openai", "GPT-4o", 0.0025, 0.01),
            ("gpt-4o-mini", "openai", "GPT-4o Mini", 0.00015, 0.0006),
            ("gpt-4-turbo", "openai", "GPT-4 Turbo", 0.01, 0.03),
            ("gpt-4", "openai", "GPT-4", 0.03, 0.06),
            ("gpt-4-32k", "openai", "GPT-4 32K", 0.06, 0.12),
            ("gpt-3.5-turbo", "openai", "GPT-3.5 Turbo", 0.0005, 0.0015),
            ("gpt-3.5-turbo-16k", "openai", "GPT-3.5 Turbo 16K", 0.003, 0.004),
            ("gpt-4.1", "openai", "GPT-4.1", 0.002, 0.008),
            ("gpt-4.1-mini", "openai", "GPT-4.1 Mini", 0.0004, 0.0016),
            ("gpt-4.1-nano", "openai", "GPT-4.1 Nano", 0.0001, 0.0004),
            ("o1", "openai", "o1", 0.015, 0.06),
            ("o1-mini", "openai", "o1 Mini", 0.0011, 0.0044),
            ("o1-pro", "openai", "o1 Pro", 0.015, 0.06),
            ("o3", "openai", "o3", 0.01, 0.04),
            ("o3-mini", "openai", "o3 Mini", 0.0011, 0.0044),
            ("o4-mini", "openai", "o4 Mini", 0.0011, 0.0044),
            ("gpt-4o-audio", "openai", "GPT-4o Audio", 0.04, 0.08),
            ("text-embedding-3-small", "openai", "Embedding 3 Small", 2e-05, 0),
            ("text-embedding-3-large", "openai", "Embedding 3 Large", 0.00013, 0),
            ("text-embedding-ada-002", "openai", "Embedding Ada 002", 0.0001, 0),
            ("davinci-002", "openai", "Davinci 002", 0.002, 0.002),
            ("babbage-002", "openai", "Babbage 002", 0.0004, 0.0004),
            ("whisper-1", "openai", "Whisper", 0.006, 0),
            ("claude-4-sonnet", "anthropic", "Claude 4 Sonnet", 0.003, 0.015),
            ("claude-4-opus", "anthropic", "Claude 4 Opus", 0.015, 0.075),
            ("claude-3.5-sonnet", "anthropic", "Claude 3.5 Sonnet", 0.003, 0.015),
            ("claude-3.5-haiku", "anthropic", "Claude 3.5 Haiku", 0.0008, 0.004),
            ("claude-3-opus", "anthropic", "Claude 3 Opus", 0.015, 0.075),
            ("claude-3-sonnet", "anthropic", "Claude 3 Sonnet", 0.003, 0.015),
            ("claude-3-haiku", "anthropic", "Claude 3 Haiku", 0.00025, 0.00125),
            ("gemini-2.5-flash", "gemini", "Gemini 2.5 Flash", 0.00015, 0.0006),
            ("gemini-2.5-pro", "gemini", "Gemini 2.5 Pro", 0.00125, 0.01),
            ("gemini-2.0-flash", "gemini", "Gemini 2.0 Flash", 0.0001, 0.0004),
            ("gemini-2.0-pro", "gemini", "Gemini 2.0 Pro", 0.00125, 0.005),
            ("gemini-1.5-pro", "gemini", "Gemini 1.5 Pro", 0.00125, 0.005),
            ("gemini-1.5-flash", "gemini", "Gemini 1.5 Flash", 7.5e-05, 0.0003),
            ("gemini-1.0-pro", "gemini", "Gemini 1.0 Pro", 5e-05, 0.00015),
            ("gemma-3-27b", "gemini", "Gemma 3 27B", 3e-05, 0.0001),
            ("gemma-3-12b", "gemini", "Gemma 3 12B", 1.5e-05, 5e-05),
            ("gemma-3-4b", "gemini", "Gemma 3 4B", 5e-06, 1.5e-05),
            ("deepseek-chat", "deepseek", "DeepSeek V3", 0.00027, 0.0011),
            ("deepseek-reasoner", "deepseek", "DeepSeek R1", 0.00055, 0.00219),
            ("deepseek-v3-0324", "deepseek", "DeepSeek V3 0324", 0.00027, 0.0011),
            ("deepseek-r1-0528", "deepseek", "DeepSeek R1 0528", 0.00055, 0.00219),
            ("deepseek-coder", "deepseek", "DeepSeek Coder V2", 0.00014, 0.00028),
            ("moonshot-v1-8k", "moonshot", "Kimi 8K", 0.003, 0.003),
            ("moonshot-v1-32k", "moonshot", "Kimi 32K", 0.006, 0.006),
            ("moonshot-v1-128k", "moonshot", "Kimi 128K", 0.012, 0.012),
            ("kimi-latest", "moonshot", "Kimi Latest", 0.006, 0.006),
            ("moonshot-v1-auto", "moonshot", "Kimi Auto", 0.006, 0.006),
            ("kimi-k2", "moonshot", "Kimi K2", 0.004, 0.008),
            ("glm-4-flash", "zhipu", "GLM-4 Flash", 0.0001, 0.0001),
            ("glm-4-plus", "zhipu", "GLM-4 Plus", 0.007, 0.007),
            ("glm-4-air", "zhipu", "GLM-4 Air", 0.0005, 0.0005),
            ("glm-4", "zhipu", "GLM-4", 0.015, 0.015),
            ("glm-4-long", "zhipu", "GLM-4 Long", 0.001, 0.001),
            ("glm-4v-plus", "zhipu", "GLM-4V Plus", 0.01, 0.01),
            ("glm-4v", "zhipu", "GLM-4V", 0.005, 0.005),
            ("glm-4v-flash", "zhipu", "GLM-4V Flash", 0.0001, 0.0001),
            ("glm-3-turbo", "zhipu", "GLM-3 Turbo", 0.0005, 0.0005),
            ("cogview-4", "zhipu", "CogView-4", 0.05, 0),
            ("cogview-3-plus", "zhipu", "CogView-3 Plus", 0.03, 0),
            ("cogview-3", "zhipu", "CogView-3", 0.025, 0),
            ("qwen-max", "qwen", "Qwen Max", 0.002, 0.006),
            ("qwen-plus", "qwen", "Qwen Plus", 0.0008, 0.002),
            ("qwen-turbo", "qwen", "Qwen Turbo", 0.0003, 0.0006),
            ("qwen3-235b", "qwen", "Qwen3 235B", 0.001, 0.004),
            ("qwen3-32b", "qwen", "Qwen3 32B", 0.0003, 0.0009),
            ("qwen3-14b", "qwen", "Qwen3 14B", 0.00015, 0.00045),
            ("qwen3-8b", "qwen", "Qwen3 8B", 8e-05, 0.00024),
            ("qwen2.5-72b", "qwen", "Qwen2.5 72B", 0.0005, 0.002),
            ("qwen2.5-32b", "qwen", "Qwen2.5 32B", 0.0003, 0.0009),
            ("qwen2.5-14b", "qwen", "Qwen2.5 14B", 0.00015, 0.00045),
            ("qwen2.5-7b", "qwen", "Qwen2.5 7B", 0.0001, 0.0003),
            ("qwen2.5-coder-32b", "qwen", "Qwen2.5 Coder 32B", 0.0003, 0.0009),
            ("qwen2.5-coder-7b", "qwen", "Qwen2.5 Coder 7B", 0.0001, 0.0003),
            ("qwen-vl-max", "qwen", "Qwen VL Max", 0.003, 0.009),
            ("qwen-vl-plus", "qwen", "Qwen VL Plus", 0.001, 0.003),
            ("qwen-long", "qwen", "Qwen Long", 0.0005, 0.002),
            ("qwen-omni", "qwen", "Qwen Omni", 0.001, 0.003),
            ("Qwen/Qwen3-235B-A22B", "siliconflow", "Qwen3 235B (SF)", 0.001, 0.004),
            ("Qwen/Qwen3-32B", "siliconflow", "Qwen3 32B (SF)", 0.0003, 0.0009),
            ("Qwen/Qwen2.5-72B-Instruct", "siliconflow", "Qwen2.5 72B (SF)", 0.0005, 0.002),
            ("Qwen/Qwen2.5-32B-Instruct", "siliconflow", "Qwen2.5 32B (SF)", 0.0003, 0.0009),
            ("Qwen/Qwen2.5-14B-Instruct", "siliconflow", "Qwen2.5 14B (SF)", 0.00015, 0.00045),
            ("Qwen/Qwen2.5-7B-Instruct", "siliconflow", "Qwen2.5 7B (SF)", 0.0001, 0.0003),
            ("deepseek-ai/DeepSeek-V3", "siliconflow", "DeepSeek V3 (SF)", 0.00027, 0.0011),
            ("deepseek-ai/DeepSeek-R1", "siliconflow", "DeepSeek R1 (SF)", 0.00055, 0.00219),
            ("Pro/Qwen/Qwen2.5-7B-Instruct", "siliconflow", "Qwen2.5 7B Pro", 0.00015, 0.00045),
            ("THUDM/glm-4-9b-chat", "siliconflow", "GLM-4 9B (SF)", 0.0001, 0.0001),
            ("internlm/internlm2_5-7b-chat", "siliconflow", "InternLM2.5 7B", 0.0001, 0.0001),
            ("01-ai/Yi-1.5-34B-Chat", "siliconflow", "Yi 1.5 34B", 0.0002, 0.0002),
            ("BAAI/bge-large-zh-v1.5", "siliconflow", "BGE Embedding", 1e-05, 0),
            ("doubao-pro-32k", "doubao", "豆包 Pro 32K", 0.0008, 0.002),
            ("doubao-pro-128k", "doubao", "豆包 Pro 128K", 0.005, 0.009),
            ("doubao-lite-32k", "doubao", "豆包 Lite 32K", 0.0003, 0.0006),
            ("doubao-lite-128k", "doubao", "豆包 Lite 128K", 0.0008, 0.001),
            ("doubao-1.5-pro-256k", "doubao", "豆包 1.5 Pro 256K", 0.005, 0.009),
            ("doubao-1.5-pro-32k", "doubao", "豆包 1.5 Pro 32K", 0.0008, 0.002),
            ("doubao-1.5-lite-32k", "doubao", "豆包 1.5 Lite 32K", 0.0003, 0.0006),
            ("doubao-vision-pro-32k", "doubao", "豆包视觉 Pro", 0.003, 0.009),
            ("doubao-embedding", "doubao", "豆包 Embedding", 2e-05, 0),
            ("deepseek-v3", "doubao", "DeepSeek V3 (豆包)", 0.00027, 0.0011),
            ("deepseek-r1", "doubao", "DeepSeek R1 (豆包)", 0.00055, 0.00219),
            ("ernie-4.0-turbo", "baidu", "ERNIE 4.0 Turbo", 0.003, 0.009),
            ("ernie-4.0", "baidu", "ERNIE 4.0", 0.012, 0.012),
            ("ernie-3.5", "baidu", "ERNIE 3.5", 0.0008, 0.0008),
            ("ernie-speed", "baidu", "ERNIE Speed", 0.0002, 0.0002),
            ("ernie-lite", "baidu", "ERNIE Lite", 5e-05, 5e-05),
            ("ernie-tiny-8k", "baidu", "ERNIE Tiny", 1e-05, 1e-05),
            ("ernie-character", "baidu", "ERNIE Character", 0.0003, 0.0003),
            ("deepseek-v3", "baidu", "DeepSeek V3 (百度)", 0.00027, 0.0011),
            ("deepseek-r1", "baidu", "DeepSeek R1 (百度)", 0.00055, 0.00219),
            ("hunyuan-turbos-latest", "tencent", "混元 TurboS", 0.0002, 0.0008),
            ("hunyuan-turbo-latest", "tencent", "混元 Turbo", 0.0005, 0.002),
            ("hunyuan-pro", "tencent", "混元 Pro", 0.001, 0.003),
            ("hunyuan-standard", "tencent", "混元 Standard", 0.0003, 0.0009),
            ("hunyuan-lite", "tencent", "混元 Lite", 5e-05, 0.00015),
            ("hunyuan-vision", "tencent", "混元视觉", 0.003, 0.009),
            ("hunyuan-embedding", "tencent", "混元 Embedding", 2e-05, 0),
            ("deepseek-v3", "tencent", "DeepSeek V3 (混元)", 0.00027, 0.0011),
            ("deepseek-r1", "tencent", "DeepSeek R1 (混元)", 0.00055, 0.00219),
            ("abab7", "minimax", "ABAB 7", 0.001, 0.001),
            ("abab6.5s", "minimax", "ABAB 6.5s", 0.0003, 0.0003),
            ("abab6.5", "minimax", "ABAB 6.5", 0.0008, 0.0008),
            ("abab5.5", "minimax", "ABAB 5.5", 0.0005, 0.0005),
            ("yi-lightning", "lingyi", "Yi Lightning", 0.0001, 0.0001),
            ("yi-large", "lingyi", "Yi Large", 0.002, 0.002),
            ("yi-medium", "lingyi", "Yi Medium", 0.0003, 0.0003),
            ("yi-vision", "lingyi", "Yi Vision", 0.003, 0.003),
            ("yi-large-turbo", "lingyi", "Yi Large Turbo", 0.001, 0.001),
            ("mistral-large-latest", "mistral", "Mistral Large", 0.002, 0.006),
            ("mistral-medium-latest", "mistral", "Mistral Medium", 0.0008, 0.0024),
            ("mistral-small-latest", "mistral", "Mistral Small", 0.0002, 0.0006),
            ("mistral-nemo", "mistral", "Mistral Nemo", 0.00015, 0.00015),
            ("codestral-latest", "mistral", "Codestral", 0.0003, 0.0009),
            ("ministral-8b", "mistral", "Ministral 8B", 0.0001, 0.0001),
            ("ministral-3b", "mistral", "Ministral 3B", 4e-05, 4e-05),
            ("pixtral-large", "mistral", "Pixtral Large", 0.002, 0.006),
            ("mistral-embed", "mistral", "Mistral Embed", 0.0001, 0),
            ("command-r-plus", "cohere", "Command R+", 0.0025, 0.01),
            ("command-r", "cohere", "Command R", 0.0005, 0.0015),
            ("command", "cohere", "Command", 0.0003, 0.0006),
            ("command-light", "cohere", "Command Light", 8e-05, 0.00016),
            ("embed-english-v3", "cohere", "Embed English v3", 0.0001, 0),
            ("embed-multilingual-v3", "cohere", "Embed Multilingual v3", 0.0001, 0),
            ("grok-3", "xai", "Grok 3", 0.003, 0.015),
            ("grok-3-mini", "xai", "Grok 3 Mini", 0.0003, 0.0005),
            ("grok-2", "xai", "Grok 2", 0.002, 0.01),
            ("grok-2-vision", "xai", "Grok 2 Vision", 0.002, 0.01),
            ("sonar-pro", "perplexity", "Sonar Pro", 0.003, 0.015),
            ("sonar", "perplexity", "Sonar", 0.001, 0.001),
            ("sonar-reasoning-pro", "perplexity", "Sonar Reasoning Pro", 0.003, 0.015),
            ("sonar-reasoning", "perplexity", "Sonar Reasoning", 0.001, 0.005),
            ("anthropic/claude-4-sonnet", "openrouter", "Claude 4 Sonnet (OR)", 0.003, 0.015),
            ("openai/gpt-4o", "openrouter", "GPT-4o (OR)", 0.005, 0.015),
            ("google/gemini-2.5-pro", "openrouter", "Gemini 2.5 Pro (OR)", 0.00125, 0.01),
            ("meta-llama/llama-4-maverick", "openrouter", "Llama 4 Maverick", 0.0002, 0.0006),
            ("meta-llama/llama-4-scout", "openrouter", "Llama 4 Scout", 0.0001, 0.0003),
            ("meta-llama/llama-3.3-70b", "openrouter", "Llama 3.3 70B", 0.0003, 0.0004),
            ("qwen/qwen-max", "openrouter", "Qwen Max (OR)", 0.002, 0.006),
            ("deepseek/deepseek-chat", "openrouter", "DeepSeek V3 (OR)", 0.00027, 0.0011),
            ("mistralai/mistral-large", "openrouter", "Mistral Large (OR)", 0.002, 0.006),
            ("spark-4.0-ultra", "xunfei", "Spark 4.0 Ultra", 0.005, 0.005),
            ("spark-max", "xunfei", "Spark Max", 0.003, 0.003),
            ("spark-pro", "xunfei", "Spark Pro", 0.001, 0.001),
            ("spark-lite", "xunfei", "Spark Lite", 0.0001, 0.0001),
            ("360gpt-pro", "360ai", "360GPT Pro", 0.0005, 0.0005),
            ("360gpt-turbo", "360ai", "360GPT Turbo", 0.0002, 0.0002),
            ("360gpt2-o1", "360ai", "360GPT2 o1", 0.0008, 0.0008),
            ("step-2-16k", "stepfun", "Step-2 16K", 0.003, 0.009),
            ("step-1-8k", "stepfun", "Step-1 8K", 0.0005, 0.002),
            ("step-1v-8k", "stepfun", "Step-1V 8K", 0.001, 0.003),
            ("step-1-flash", "stepfun", "Step-1 Flash", 0.0001, 0.0004),
            ("baichuan4", "baichuan", "Baichuan 4", 0.005, 0.005),
            ("baichuan3-turbo", "baichuan", "Baichuan 3 Turbo", 0.001, 0.001),
            ("baichuan3-turbo-128k", "baichuan", "Baichuan 3 Turbo 128K", 0.002, 0.002),
            ("baichuan2-turbo", "baichuan", "Baichuan 2 Turbo", 0.0005, 0.0005),
            ("baichuan2-53b", "baichuan", "Baichuan 2 53B", 0.0008, 0.0008),
            ("llama-4-maverick-17b", "groq", "Llama 4 Maverick (Groq)", 0.0002, 0.0006),
            ("llama-4-scout-17b", "groq", "Llama 4 Scout (Groq)", 0.0001, 0.0003),
            ("llama-3.3-70b-versatile", "groq", "Llama 3.3 70B (Groq)", 0.00029, 0.00039),
            ("deepseek-r1-distill-llama-70b", "groq", "DeepSeek R1 70B (Groq)", 0.00027, 0.0011),
            ("mixtral-8x7b-32768", "groq", "Mixtral 8x7B (Groq)", 0.00013, 0.00013),
            ("gemma2-9b-it", "groq", "Gemma 2 9B (Groq)", 4e-05, 4e-05),
            ("meta-llama/Llama-4-Maverick-17B", "together", "Llama 4 Maverick (TA)", 0.0006, 0.0008),
            ("meta-llama/Llama-4-Scout-17B", "together", "Llama 4 Scout (TA)", 0.0003, 0.0004),
            ("meta-llama/Meta-Llama-3.3-70B-Instruct", "together", "Llama 3.3 70B (TA)", 0.00029, 0.00039),
            ("deepseek-ai/DeepSeek-V3", "together", "DeepSeek V3 (TA)", 0.00027, 0.0011),
            ("Qwen/Qwen2.5-72B-Instruct", "together", "Qwen2.5 72B (TA)", 0.0005, 0.002),
            ("mistralai/Mixtral-8x22B", "together", "Mixtral 8x22B (TA)", 0.0003, 0.0003),
            ("accounts/fireworks/models/llama-v3p3-70b-instruct", "fireworks", "Llama 3.3 70B (FW)", 0.00029, 0.00039),
            ("accounts/fireworks/models/deepseek-v3", "fireworks", "DeepSeek V3 (FW)", 0.00027, 0.0011),
            ("accounts/fireworks/models/mixtral-8x22b-instruct", "fireworks", "Mixtral 8x22B (FW)", 0.0003, 0.0003),
            ("jina-embeddings-v3", "jina", "Jina Embeddings v3", 2e-05, 0),
            ("jina-reranker-v2", "jina", "Jina Reranker v2", 3e-05, 0),
            ("voyage-3-large", "voyage", "Voyage 3 Large", 0.00018, 0),
            ("voyage-3", "voyage", "Voyage 3", 6e-05, 0),
            ("voyage-3-lite", "voyage", "Voyage 3 Lite", 2e-05, 0),
            ("voyage-code-3", "voyage", "Voyage Code 3", 6e-05, 0),
            ("voyage-law-2", "voyage", "Voyage Law 2", 6e-05, 0),
            ("gpt-4o-2024-08-06", "openai", "GPT-4o Aug 2024", 0.0025, 0.01),
            ("gpt-4o-2024-05-13", "openai", "GPT-4o May 2024", 0.005, 0.015),
            ("gpt-4-0613", "openai", "GPT-4 Jun 2024", 0.03, 0.06),
            ("gpt-4-0314", "openai", "GPT-4 Mar 2024", 0.03, 0.06),
            ("gpt-4-turbo-2024-04-09", "openai", "GPT-4 Turbo Apr 2024", 0.01, 0.03),
            ("gpt-3.5-turbo-0125", "openai", "GPT-3.5 Turbo Jan 2024", 0.0005, 0.0015),
            ("gpt-3.5-turbo-1106", "openai", "GPT-3.5 Turbo Nov 2024", 0.001, 0.002),
            ("gpt-3.5-turbo-instruct", "openai", "GPT-3.5 Turbo Instruct", 0.0015, 0.002),
            ("o1-preview", "openai", "o1 Preview", 0.015, 0.06),
            ("o3-pro", "openai", "o3 Pro", 0.01, 0.04),
            ("codex-latest", "openai", "Codex Latest", 0.005, 0.015),
            ("dall-e-3", "openai", "DALL-E 3", 0.04, 0),
            ("dall-e-2", "openai", "DALL-E 2", 0.02, 0),
            ("tts-1", "openai", "TTS-1", 0.015, 0),
            ("tts-1-hd", "openai", "TTS-1 HD", 0.03, 0),
            ("gpt-4o-mini-2024-07-18", "openai", "GPT-4o Mini Jul 2024", 0.00015, 0.0006),
            ("claude-2.1", "anthropic", "Claude 2.1", 0.008, 0.024),
            ("claude-2.0", "anthropic", "Claude 2.0", 0.008, 0.024),
            ("claude-instant-1.2", "anthropic", "Claude Instant 1.2", 0.0008, 0.0024),
            ("gemini-2.0-flash-thinking", "gemini", "Gemini 2.0 Flash Thinking", 0.00015, 0.0006),
            ("gemini-1.5-flash-8b", "gemini", "Gemini 1.5 Flash 8B", 4e-05, 0.00015),
            ("gemini-pro-vision", "gemini", "Gemini Pro Vision", 0.0005, 0.0015),
            ("gemma-2-27b", "gemini", "Gemma 2 27B", 3e-05, 0.0001),
            ("gemma-2-9b", "gemini", "Gemma 2 9B", 1.5e-05, 5e-05),
            ("gemma-2-2b", "gemini", "Gemma 2 2B", 5e-06, 1.5e-05),
            ("palm-2-chat", "gemini", "PaLM 2 Chat", 0.0005, 0.0005),
            ("palm-2-text", "gemini", "PaLM 2 Text", 0.0003, 0.0003),
            ("medlm", "gemini", "MedLM", 0.005, 0.005),
            ("codey", "gemini", "Codey", 0.0005, 0.0005),
            ("deepseek-v2.5", "deepseek", "DeepSeek V2.5", 0.00014, 0.00028),
            ("deepseek-coder-v2-instruct", "deepseek", "DeepSeek Coder V2 Instruct", 0.00014, 0.00028),
            ("deepseek-llm-67b-chat", "deepseek", "DeepSeek LLM 67B", 0.0002, 0.0002),
            ("deepseek-v2", "deepseek", "DeepSeek V2", 0.00014, 0.00028),
            ("qwen2.5-1.5b", "qwen", "Qwen2.5 1.5B", 2e-05, 6e-05),
            ("qwen2.5-3b", "qwen", "Qwen2.5 3B", 4e-05, 0.00012),
            ("qwen2.5-0.5b", "qwen", "Qwen2.5 0.5B", 1e-05, 3e-05),
            ("qwen2.5-math-72b", "qwen", "Qwen2.5 Math 72B", 0.0005, 0.002),
            ("qwen2.5-math-7b", "qwen", "Qwen2.5 Math 7B", 0.0001, 0.0003),
            ("qwen3-1.7b", "qwen", "Qwen3 1.7B", 2e-05, 6e-05),
            ("qwen3-4b", "qwen", "Qwen3 4B", 4e-05, 0.00012),
            ("qwen-audio", "qwen", "Qwen Audio", 0.001, 0.003),
            ("qwen2.5-vl-72b", "qwen", "Qwen2.5 VL 72B", 0.003, 0.009),
            ("qwen2.5-vl-7b", "qwen", "Qwen2.5 VL 7B", 0.001, 0.003),
            ("qwen2.5-vl-3b", "qwen", "Qwen2.5 VL 3B", 0.0005, 0.0015),
            ("Pro/deepseek-ai/DeepSeek-V3", "siliconflow", "DeepSeek V3 Pro (SF)", 0.0005, 0.0015),
            ("meta-llama/Llama-4-Maverick-17B", "siliconflow", "Llama 4 Maverick (SF)", 0.0002, 0.0006),
            ("meta-llama/Llama-4-Scout-17B", "siliconflow", "Llama 4 Scout (SF)", 0.0001, 0.0003),
            ("Pro/meta-llama/Llama-4-Maverick-17B", "siliconflow", "Llama 4 Maverick Pro", 0.0004, 0.0008),
            ("deepseek-ai/DeepSeek-R1-Distill-Qwen-32B", "siliconflow", "DeepSeek R1 32B (SF)", 0.00027, 0.0011),
            ("deepseek-ai/DeepSeek-R1-Distill-Qwen-14B", "siliconflow", "DeepSeek R1 14B (SF)", 0.00015, 0.00045),
            ("deepseek-ai/DeepSeek-R1-Distill-Qwen-7B", "siliconflow", "DeepSeek R1 7B (SF)", 0.0001, 0.0003),
            ("deepseek-ai/DeepSeek-R1-Distill-Llama-8B", "siliconflow", "DeepSeek R1 Llama 8B", 0.0001, 0.0003),
            ("BAAI/bge-m3", "siliconflow", "BGE-M3 Embedding", 2e-05, 0),
            ("google/gemini-2.5-flash", "openrouter", "Gemini 2.5 Flash (OR)", 0.00015, 0.0006),
            ("x-ai/grok-3", "openrouter", "Grok 3 (OR)", 0.003, 0.015),
            ("anthropic/claude-4-opus", "openrouter", "Claude 4 Opus (OR)", 0.015, 0.075),
            ("deepseek/deepseek-r1", "openrouter", "DeepSeek R1 (OR)", 0.00055, 0.00219),
            ("mistralai/codestral", "openrouter", "Codestral (OR)", 0.0003, 0.0009),
            ("cohere/command-r-plus", "openrouter", "Command R+ (OR)", 0.0025, 0.01),
            ("meta-llama/llama-3.2-90b", "openrouter", "Llama 3.2 90B", 0.0002, 0.0005),
            ("meta-llama/llama-3.2-11b", "openrouter", "Llama 3.2 11B", 5e-05, 0.00015),
            ("meta-llama/llama-3.2-3b", "openrouter", "Llama 3.2 3B", 1e-05, 3e-05),
            ("meta-llama/llama-3.2-1b", "openrouter", "Llama 3.2 1B", 5e-06, 1.5e-05),
            ("microsoft/phi-4", "openrouter", "Phi-4", 5e-05, 0.0001),
            ("microsoft/phi-4-mini", "openrouter", "Phi-4 Mini", 2e-05, 5e-05),
            ("qwen/qwen2.5-72b", "openrouter", "Qwen2.5 72B (OR)", 0.0005, 0.002),
            ("qwen/qwen2.5-32b", "openrouter", "Qwen2.5 32B (OR)", 0.0003, 0.0009),
            ("nousresearch/hermes-3-llama-3.1-405b", "openrouter", "Hermes 3 405B", 0.003, 0.003),
            ("doubao-1.5-thinking-pro", "doubao", "豆包 1.5 Thinking Pro", 0.001, 0.004),
            ("doubao-function-call", "doubao", "豆包 Function Call", 0.0008, 0.002),
            ("seed-tts", "doubao", "SeedTTS", 0.005, 0),
            ("hunyuan-a13b", "tencent", "混元 A13B", 0.0001, 0.0003),
            ("hunyuan-t1", "tencent", "混元 T1", 0.0003, 0.0009),
            ("hunyuan-large", "tencent", "混元 Large", 0.002, 0.006),
            ("hunyuan-code", "tencent", "混元 Code", 0.0005, 0.0015),
            ("llama-3.1-8b-instant", "groq", "Llama 3.1 8B (Groq)", 2e-05, 2e-05),
            ("llama-3.2-90b-vision-preview", "groq", "Llama 3.2 90B Vision (Groq)", 0.0002, 0.0005),
            ("llama-3.2-11b-vision-preview", "groq", "Llama 3.2 11B Vision (Groq)", 5e-05, 0.00015),
            ("llama-3.2-3b-preview", "groq", "Llama 3.2 3B (Groq)", 1e-05, 3e-05),
            ("llama-guard-3-8b", "groq", "Llama Guard 3 8B", 2e-05, 2e-05),
            ("Qwen/Qwen2.5-32B-Instruct", "together", "Qwen2.5 32B (TA)", 0.0003, 0.0009),
            ("Qwen/Qwen2.5-14B-Instruct", "together", "Qwen2.5 14B (TA)", 0.00015, 0.00045),
            ("Qwen/Qwen2.5-7B-Instruct", "together", "Qwen2.5 7B (TA)", 0.0001, 0.0003),
            ("deepseek-ai/DeepSeek-R1", "together", "DeepSeek R1 (TA)", 0.00055, 0.00219),
            ("meta-llama/Llama-3.2-3B-Instruct", "together", "Llama 3.2 3B (TA)", 1e-05, 3e-05),
            ("google/gemma-2-27b-it", "together", "Gemma 2 27B (TA)", 3e-05, 0.0001),
            ("google/gemma-2-9b-it", "together", "Gemma 2 9B (TA)", 1.5e-05, 5e-05),
            ("meta/llama-4-maverick", "replicate", "Llama 4 Maverick (Rep)", 0.0003, 0.0006),
            ("meta/llama-4-scout", "replicate", "Llama 4 Scout (Rep)", 0.00015, 0.0003),
            ("meta/llama-3.3-70b", "replicate", "Llama 3.3 70B (Rep)", 0.0004, 0.0004),
            ("mistralai/mixtral-8x7b", "replicate", "Mixtral 8x7B (Rep)", 0.0002, 0.0002),
            ("code-geex-4", "zhipu", "CodeGeeX 4", 0.0001, 0.0001),
            ("charglm-4", "zhipu", "CharGLM-4", 0.005, 0.005),
            ("emohaa", "zhipu", "Emohaa", 0.005, 0.005),
            ("open-mistral-7b", "mistral", "Open Mistral 7B", 7e-05, 7e-05),
            ("open-mixtral-8x7b", "mistral", "Open Mixtral 8x7B", 0.0002, 0.0002),
            ("open-mixtral-8x22b", "mistral", "Open Mixtral 8x22B", 0.0005, 0.0005),
            ("grok-2-1212", "xai", "Grok 2 Dec 2024", 0.002, 0.01),
            ("grok-2-vision-1212", "xai", "Grok 2 Vision Dec", 0.002, 0.01),
            ("ernie-speed-128k", "baidu", "ERNIE Speed 128K", 0.0004, 0.0004),
            ("ernie-4.0-8k", "baidu", "ERNIE 4.0 8K", 0.012, 0.012),
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO models (id, provider_id, display_name, input_price, output_price) VALUES (?, ?, ?, ?, ?)",
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
