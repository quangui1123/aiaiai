import json, time, uuid
from typing import AsyncIterator, Optional
import httpx
from app.models import ChatRequest, ChatResponse, ChatChoice, Usage, Message


class ProviderAdapter:
    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=120.0)

    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def chat(self, req: ChatRequest) -> ChatResponse:
        raise NotImplementedError

    async def chat_stream(self, req: ChatRequest) -> AsyncIterator[str]:
        raise NotImplementedError

    async def close(self):
        await self.client.aclose()


def _openai_sse_chunk(model: str, content: str = "", finish_reason: Optional[str] = None, usage: Optional[dict] = None) -> str:
    chunk = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
    }
    if content:
        chunk["choices"][0]["delta"] = {"content": content}
    if usage:
        chunk["usage"] = usage
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


class OpenAICompatibleAdapter(ProviderAdapter):
    async def chat(self, req: ChatRequest) -> ChatResponse:
        body = {
            "model": req.model,
            "messages": [m.model_dump() for m in req.messages],
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "top_p": req.top_p,
            "stream": False,
            "stop": req.stop,
        }
        body = {k: v for k, v in body.items() if v is not None}
        resp = await self.client.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=body)
        resp.raise_for_status()
        data = resp.json()
        return ChatResponse(
            id=data.get("id", f"chatcmpl-{uuid.uuid4().hex[:12]}"),
            created=int(time.time()),
            model=req.model,
            choices=[
                ChatChoice(
                    index=c["index"],
                    message=Message(role=c["message"]["role"], content=c["message"].get("content") or ""),
                    finish_reason=c.get("finish_reason", "stop"),
                ) for c in data["choices"]
            ],
            usage=Usage(
                prompt_tokens=data.get("usage", {}).get("prompt_tokens", 0),
                completion_tokens=data.get("usage", {}).get("completion_tokens", 0),
                total_tokens=data.get("usage", {}).get("total_tokens", 0),
            ),
        )

    async def chat_stream(self, req: ChatRequest) -> AsyncIterator[str]:
        body = {
            "model": req.model,
            "messages": [m.model_dump() for m in req.messages],
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "top_p": req.top_p,
            "stream": True,
            "stop": req.stop,
        }
        body = {k: v for k, v in body.items() if v is not None}
        async with self.client.stream("POST", f"{self.base_url}/chat/completions", headers=self._headers(), json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    yield line + "\n\n"
                elif line.strip() == "[DONE]":
                    yield "data: [DONE]\n\n"


class AnthropicAdapter(ProviderAdapter):
    def _convert(self, messages):
        system = None
        converted = []
        for m in messages:
            if m.role == "system":
                system = m.content
            elif m.role in ("user", "assistant"):
                converted.append({"role": m.role, "content": m.content})
            else:
                converted.append({"role": "user", "content": m.content})
        return converted, system

    async def chat(self, req: ChatRequest) -> ChatResponse:
        msgs, system = self._convert(req.messages)
        body = {
            "model": req.model,
            "messages": msgs,
            "max_tokens": req.max_tokens or 4096,
            "temperature": req.temperature,
            "top_p": req.top_p,
            "stop_sequences": req.stop,
        }
        if system:
            body["system"] = system
        body = {k: v for k, v in body.items() if v is not None}
        resp = await self.client.post(
            f"{self.base_url}/messages",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        content = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        usage_data = data.get("usage", {})
        prompt_tokens = usage_data.get("input_tokens", 0)
        completion_tokens = usage_data.get("output_tokens", 0)
        return ChatResponse(
            id=data["id"],
            created=int(time.time()),
            model=req.model,
            choices=[ChatChoice(index=0, message=Message(role="assistant", content=content), finish_reason=data.get("stop_reason", "end_turn"))],
            usage=Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, total_tokens=prompt_tokens + completion_tokens),
        )

    async def chat_stream(self, req: ChatRequest) -> AsyncIterator[str]:
        msgs, system = self._convert(req.messages)
        body = {"model": req.model, "messages": msgs, "max_tokens": req.max_tokens or 4096, "temperature": req.temperature, "top_p": req.top_p, "stop_sequences": req.stop, "stream": True}
        if system:
            body["system"] = system
        body = {k: v for k, v in body.items() if v is not None}
        prompt_tokens = 0
        completion_tokens = 0
        sent_role = False

        async with self.client.stream(
            "POST", f"{self.base_url}/messages",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
            json=body,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    event = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                etype = event.get("type")
                if etype == "message_start":
                    usage = event.get("message", {}).get("usage", {})
                    prompt_tokens = usage.get("input_tokens", prompt_tokens)
                elif etype == "content_block_delta":
                    text = event.get("delta", {}).get("text", "")
                    if text:
                        if not sent_role:
                            yield _openai_sse_chunk(req.model)
                            sent_role = True
                        yield _openai_sse_chunk(req.model, content=text)
                elif etype == "message_delta":
                    usage = event.get("usage", {})
                    completion_tokens = usage.get("output_tokens", completion_tokens)
                elif etype == "message_stop":
                    break

        yield _openai_sse_chunk(req.model, finish_reason="stop", usage={"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": prompt_tokens + completion_tokens})
        yield "data: [DONE]\n\n"


class GeminiAdapter(ProviderAdapter):
    def _build_body(self, req: ChatRequest):
        contents = []
        system_instruction = None
        for m in req.messages:
            if m.role == "system":
                system_instruction = {"parts": [{"text": m.content}]}
            else:
                role = "model" if m.role == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": m.content}]})
        body = {"contents": contents, "generationConfig": {"temperature": req.temperature, "topP": req.top_p, "maxOutputTokens": req.max_tokens or 4096, "stopSequences": req.stop or []}}
        if system_instruction:
            body["systemInstruction"] = system_instruction
        return body

    async def chat(self, req: ChatRequest) -> ChatResponse:
        body = self._build_body(req)
        url = f"{self.base_url}/models/{req.model}:generateContent"
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        resp = await self.client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        text = ""
        candidates = data.get("candidates") or []
        if candidates:
            for part in candidates[0].get("content", {}).get("parts", []):
                text += part.get("text", "")
        usage = data.get("usageMetadata", {})
        prompt_tokens = usage.get("promptTokenCount", 0)
        completion_tokens = usage.get("candidatesTokenCount", 0)
        return ChatResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model=req.model,
            choices=[ChatChoice(index=0, message=Message(role="assistant", content=text), finish_reason=candidates[0].get("finishReason", "STOP") if candidates else "stop")],
            usage=Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, total_tokens=usage.get("totalTokenCount", prompt_tokens + completion_tokens)),
        )

    async def chat_stream(self, req: ChatRequest) -> AsyncIterator[str]:
        body = self._build_body(req)
        url = f"{self.base_url}/models/{req.model}:streamGenerateContent?alt=sse"
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        prompt_tokens = 0
        completion_tokens = 0
        sent_role = False

        async with self.client.stream("POST", url, json=body, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                usage = data.get("usageMetadata", {})
                prompt_tokens = usage.get("promptTokenCount", prompt_tokens)
                completion_tokens = usage.get("candidatesTokenCount", completion_tokens)
                for candidate in data.get("candidates", []):
                    for part in candidate.get("content", {}).get("parts", []):
                        text = part.get("text", "")
                        if text:
                            if not sent_role:
                                yield _openai_sse_chunk(req.model)
                                sent_role = True
                            yield _openai_sse_chunk(req.model, content=text)

        yield _openai_sse_chunk(req.model, finish_reason="stop", usage={"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": prompt_tokens + completion_tokens})
        yield "data: [DONE]\n\n"


async def create_adapter(provider_id: str, api_key: str, base_url: str) -> ProviderAdapter:
    if provider_id == "anthropic":
        return AnthropicAdapter(api_key, base_url)
    if provider_id == "gemini":
        return GeminiAdapter(api_key, base_url)
    return OpenAICompatibleAdapter(api_key, base_url)
