import httpx, json, time, uuid
from typing import AsyncIterator
from models import ChatRequest, ChatResponse, ChatChoice, Usage, Message


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


class OpenAICompatibleAdapter(ProviderAdapter):
    async def chat(self, req: ChatRequest) -> ChatResponse:
        body = {
            "model": req.model, "messages": [m.model_dump() for m in req.messages],
            "max_tokens": req.max_tokens, "temperature": req.temperature,
            "top_p": req.top_p, "stream": False, "stop": req.stop
        }
        body = {k: v for k, v in body.items() if v is not None}
        resp = await self.client.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=body)
        resp.raise_for_status()
        data = resp.json()
        return ChatResponse(
            id=data.get("id", f"chatcmpl-{uuid.uuid4().hex[:12]}"),
            created=int(time.time()), model=req.model,
            choices=[ChatChoice(
                index=c["index"],
                message=Message(role=c["message"]["role"], content=c["message"]["content"]),
                finish_reason=c.get("finish_reason", "stop")
            ) for c in data["choices"]],
            usage=Usage(
                prompt_tokens=data.get("usage", {}).get("prompt_tokens", 0),
                completion_tokens=data.get("usage", {}).get("completion_tokens", 0),
                total_tokens=data.get("usage", {}).get("total_tokens", 0)
            )
        )

    async def chat_stream(self, req: ChatRequest) -> AsyncIterator[str]:
        body = {
            "model": req.model, "messages": [m.model_dump() for m in req.messages],
            "max_tokens": req.max_tokens, "temperature": req.temperature,
            "top_p": req.top_p, "stream": True, "stop": req.stop
        }
        body = {k: v for k, v in body.items() if v is not None}
        async with self.client.stream("POST", f"{self.base_url}/chat/completions", headers=self._headers(), json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    yield line + "\n\n"


class AnthropicAdapter(ProviderAdapter):
    def _convert(self, messages):
        system = None
        converted = []
        for m in messages:
            if m.role == "system":
                system = m.content
            else:
                converted.append({"role": m.role, "content": m.content})
        return converted, system

    async def chat(self, req: ChatRequest) -> ChatResponse:
        msgs, system = self._convert(req.messages)
        body = {
            "model": req.model, "messages": msgs,
            "max_tokens": req.max_tokens or 4096,
            "temperature": req.temperature, "top_p": req.top_p,
            "stop_sequences": req.stop
        }
        if system:
            body["system"] = system
        body = {k: v for k, v in body.items() if v is not None}
        resp = await self.client.post(
            f"{self.base_url}/messages",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
            json=body
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["content"][0]["text"]
        return ChatResponse(
            id=data["id"], created=int(time.time()), model=req.model,
            choices=[ChatChoice(
                index=0, message=Message(role="assistant", content=content),
                finish_reason=data.get("stop_reason", "end_turn")
            )],
            usage=Usage(
                prompt_tokens=data.get("usage", {}).get("input_tokens", 0),
                completion_tokens=data.get("usage", {}).get("output_tokens", 0),
                total_tokens=data.get("usage", {}).get("input_tokens", 0) + data.get("usage", {}).get("output_tokens", 0)
            )
        )

    async def chat_stream(self, req: ChatRequest) -> AsyncIterator[str]:
        msgs, system = self._convert(req.messages)
        body = {
            "model": req.model, "messages": msgs,
            "max_tokens": req.max_tokens or 4096,
            "temperature": req.temperature, "top_p": req.top_p,
            "stop_sequences": req.stop, "stream": True
        }
        if system:
            body["system"] = system
        body = {k: v for k, v in body.items() if v is not None}
        async with self.client.stream("POST", f"{self.base_url}/messages",
                                      headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
                                      json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    yield line + "\n\n"


class GeminiAdapter(ProviderAdapter):
    async def chat(self, req: ChatRequest) -> ChatResponse:
        contents = []
        system_instruction = None
        for m in req.messages:
            if m.role == "system":
                system_instruction = {"parts": [{"text": m.content}]}
            else:
                role = "model" if m.role == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": m.content}]})
        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": req.temperature, "topP": req.top_p,
                "maxOutputTokens": req.max_tokens or 4096,
                "stopSequences": req.stop or []
            }
        }
        if system_instruction:
            body["systemInstruction"] = system_instruction
        url = f"{self.base_url}/models/{req.model}:generateContent?key={self.api_key}"
        resp = await self.client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0].get("text", "")
        usage = data.get("usageMetadata", {})
        return ChatResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}", created=int(time.time()), model=req.model,
            choices=[ChatChoice(
                index=0, message=Message(role="assistant", content=text),
                finish_reason=data["candidates"][0].get("finishReason", "STOP")
            )],
            usage=Usage(
                prompt_tokens=usage.get("promptTokenCount", 0),
                completion_tokens=usage.get("candidatesTokenCount", 0),
                total_tokens=usage.get("totalTokenCount", 0)
            )
        )

    async def chat_stream(self, req: ChatRequest) -> AsyncIterator[str]:
        contents = []
        system_instruction = None
        for m in req.messages:
            if m.role == "system":
                system_instruction = {"parts": [{"text": m.content}]}
            else:
                role = "model" if m.role == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": m.content}]})
        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": req.temperature, "topP": req.top_p,
                "maxOutputTokens": req.max_tokens or 4096,
                "stopSequences": req.stop or []
            }
        }
        if system_instruction:
            body["systemInstruction"] = system_instruction
        url = f"{self.base_url}/models/{req.model}:streamGenerateContent?alt=sse&key={self.api_key}"
        async with self.client.stream("POST", url, json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    yield line + "\n\n"


async def create_adapter(provider_id: str, api_key: str, base_url: str) -> ProviderAdapter:
    if provider_id == "anthropic":
        return AnthropicAdapter(api_key, base_url)
    if provider_id == "gemini":
        return GeminiAdapter(api_key, base_url)
    return OpenAICompatibleAdapter(api_key, base_url)
