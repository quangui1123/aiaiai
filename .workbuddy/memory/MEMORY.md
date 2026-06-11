# 项目记忆 — ai-token-proxy

## 项目概述
统一 LLM API 代理网关 (FastAPI + SQLite)，支持多 Provider（OpenAI 兼容、Anthropic、Gemini）的统一 API Token 管理和配额控制。

## 技术栈
- Python 3.13 + FastAPI + httpx
- SQLite (WAL 模式, FK 约束)
- JWT 认证 (HS256)
- Pydantic 数据模型
- SSE 流式代理

## 关键架构
- **适配器模式**: providers.py 中 OpenAICompatibleAdapter / AnthropicAdapter / GeminiAdapter
- **数据库 FK 依赖链**: rate_limits → usage_logs → user_tokens → users
- **认证双通道**: API Token (Bearer) 用于 LLM API 调用；JWT 用于 Web 管理后台

## 已知注意事项
- Gemini API key 必须通过 Header `x-goog-api-key` 传递，不要放在 URL 参数中
- `effective_rpm()` 使用 `is not None` 检查而非 `or` 短路
- `resolve_model()` 的 `_seen` 参数防止循环引用
- 静态 HTML 中 `by_provider` / `by_model` 必须做空值保护
- 项目文件不能有 UTF-8 BOM (`\ufeff`)，会导致 Python 3.13 SyntaxError 和 Render YAML 解析失败

## 部署
- Render: workspace `tea-d8ie8cjtqb8s73b2d0e0`, 使用 `render.yaml` (native Python runtime)
- 端口由 `$PORT` 环境变量控制，不要硬编码
- SQLite 数据持久化挂载在 `/app/data`
- Dockerfile 备用 (Railway 兼容)
