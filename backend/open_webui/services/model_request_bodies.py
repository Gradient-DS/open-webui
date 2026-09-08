"""Declare model inputs for OpenAPI while preserving raw payloads for handlers.

The dependencies validate a separate body parameter, then return Starlette's
cached JSON. Do not return model_dump(): it inserts defaults, coerces values,
and changes the dictionaries also used by internal callers of these handlers.
Vendor extensions and arbitrary tool JSON schemas remain permitted extras.
"""

from typing import Any

from fastapi import Request
from pydantic import BaseModel, ConfigDict


class ImageURL(BaseModel):
    model_config = ConfigDict(extra='allow')
    url: str | None = None
    detail: str | None = None


class ContentSource(BaseModel):
    model_config = ConfigDict(extra='allow')
    type: str | None = None
    media_type: str | None = None
    data: str | None = None
    url: str | None = None


class InputAudio(BaseModel):
    model_config = ConfigDict(extra='allow')
    data: str | None = None
    format: str | None = None


class ContentBlock(BaseModel):
    model_config = ConfigDict(extra='allow')
    type: str | None = None
    text: str | None = None
    image_url: ImageURL | str | None = None
    source: ContentSource | None = None
    input_audio: InputAudio | None = None
    id: str | None = None
    name: str | None = None
    tool_use_id: str | None = None
    thinking: str | None = None
    signature: str | None = None
    content: str | list['ContentBlock'] | None = None


class ToolFunction(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str | None = None
    description: str | None = None
    # OpenAI sends serialized arguments; Ollama also accepts an arbitrary object.
    arguments: str | dict[str, Any] | None = None


class ToolCall(BaseModel):
    model_config = ConfigDict(extra='allow')
    id: str | None = None
    type: str | None = None
    function: ToolFunction | None = None


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra='allow')
    type: str | None = None
    function: ToolFunction | None = None
    name: str | None = None
    description: str | None = None


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra='allow')
    role: str | None = None
    content: str | list[ContentBlock] | None = None
    name: str | None = None
    id: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall | None] | None = None
    function_call: ToolFunction | None = None
    images: list[str | None] | None = None
    thinking: str | None = None
    reasoning_content: str | None = None


class ChatCompletionBody(BaseModel):
    model_config = ConfigDict(extra='allow')
    model: str | None = None
    messages: list[ChatMessage] | None = None
    tools: list[ToolDefinition | None] | None = None
    system: str | list[ContentBlock] | None = None
    template: str | None = None
    chat_id: str | None = None
    id: str | None = None
    session_id: str | None = None
    parent_id: str | None = None
    user_message: ChatMessage | None = None
    parent_message: ChatMessage | None = None


class TaskCompletionBody(BaseModel):
    model_config = ConfigDict(extra='allow')
    model: str | None = None
    messages: list[ChatMessage] | None = None
    prompt: str | None = None
    responses: list[str] | None = None
    type: str | None = None
    chat_id: str | None = None


class CompletionBody(BaseModel):
    model_config = ConfigDict(extra='allow')
    model: str | None = None
    prompt: str | list[str] | list[int] | list[list[int]] | None = None
    suffix: str | None = None


class MessagesBody(BaseModel):
    model_config = ConfigDict(extra='allow')
    model: str | None = None
    messages: list[ChatMessage] | None = None
    system: str | list[ContentBlock] | None = None
    tools: list[ToolDefinition] | None = None
    stop_sequences: list[str] | None = None


class EmbeddingsBody(BaseModel):
    model_config = ConfigDict(extra='allow')
    model: str | None = None
    input: str | list[str] | list[int] | list[list[int]] | None = None


async def chat_completion_body(request: Request, body: ChatCompletionBody) -> dict:
    return await request.json()


async def task_completion_body(request: Request, body: TaskCompletionBody) -> dict:
    return await request.json()


async def completion_body(request: Request, body: CompletionBody) -> dict:
    return await request.json()


async def messages_body(request: Request, body: MessagesBody) -> dict:
    return await request.json()


async def embeddings_body(request: Request, body: EmbeddingsBody) -> dict:
    return await request.json()
