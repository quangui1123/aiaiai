from pydantic import BaseModel, Field
from typing import Optional


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[Message]
    max_tokens: Optional[int] = Field(default=None)
    temperature: float = 0.7
    top_p: float = 1.0
    stream: bool = False
    stop: Optional[list[str]] = None
    provider: Optional[str] = None


class ChatChoice(BaseModel):
    index: int = 0
    message: Message
    finish_reason: Optional[str] = "stop"


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatChoice]
    usage: Usage


class ModelItem(BaseModel):
    id: str
    object: str = "model"
    created: int
    owned_by: str


class ModelList(BaseModel):
    object: str = "list"
    data: list[ModelItem]


class ErrorResponse(BaseModel):
    error: dict


class TokenCreateRequest(BaseModel):
    name: str
    email: str = ""
    role: str = "user"
    quota: float = -1
    rate_limit_rpm: int = 60


class TokenUpdateRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    quota: Optional[float] = None
    rate_limit_rpm: Optional[int] = None
    enabled: Optional[bool] = None


class QuotaAddRequest(BaseModel):
    amount: float


class ChannelRequest(BaseModel):
    provider_id: str
    name: str = ""
    api_key: str
    weight: int = 1


class ChannelUpdateRequest(BaseModel):
    name: Optional[str] = None
    api_key: Optional[str] = None
    weight: Optional[int] = None
    enabled: Optional[bool] = None


class ModelRequest(BaseModel):
    model_id: str
    provider_id: str
    display_name: str
    input_price: float
    output_price: float
    unit_size: int = 1000
