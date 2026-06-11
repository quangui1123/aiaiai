import aiosqlite
from contextlib import asynccontextmanager
from app.config import settings


@asynccontextmanager
async def get_db():
    conn = await aiosqlite.connect(settings.db_path, timeout=10)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        await conn.close()


async def init_db():
    async with get_db() as conn:
        await conn.executescript("""
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
            CREATE INDEX IF NOT EXISTS idx_models_id ON models(id);
            CREATE INDEX IF NOT EXISTS idx_channels_provider ON channels(provider_id, enabled);
            CREATE INDEX IF NOT EXISTS idx_usage_logs_created ON usage_logs(created_at);
            CREATE INDEX IF NOT EXISTS idx_user_tokens_user ON user_tokens(user_id);
        """)
        await conn.commit()

        count = await conn.execute_fetchall("SELECT COUNT(*) as c FROM providers")
        if count[0][0] == 0:
            await _seed_providers(conn)

        mcount = await conn.execute_fetchall("SELECT COUNT(*) as c FROM models")
        if mcount[0][0] == 0:
            await _seed_models(conn)

        await conn.commit()


async def _seed_providers(conn: aiosqlite.Connection):
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
        ("replicate", "Replicate", "https://api.replicate.com/v1"),
    ]
    await conn.executemany(
        "INSERT INTO providers (id, name, base_url) VALUES (?, ?, ?)", providers
    )


async def _seed_models(conn: aiosqlite.Connection):
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
    ]
    await conn.executemany(
        "INSERT OR REPLACE INTO models (id, provider_id, display_name, input_price, output_price) VALUES (?, ?, ?, ?, ?)",
        models
    )
