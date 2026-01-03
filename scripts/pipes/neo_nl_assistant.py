"""
title: NEO NL Document Assistant
description: Search nuclear safety documents via MCP and generate responses with citations
author: NEO NL Team
version: 0.6.0
requirements: aiohttp
"""

from pydantic import BaseModel, Field
from typing import AsyncGenerator
import json
import logging
import os
import aiohttp

# Import MCP client from Open WebUI
from open_webui.utils.mcp.client import MCPClient

log = logging.getLogger(__name__)


# System prompt for nuclear safety domain
SYSTEM_PROMPT = """Je bent een deskundige assistent voor nucleaire veiligheid die vragen beantwoordt op basis van officiële documenten van het IAEA, ANVS en Nederlandse wetgeving.

Richtlijnen:
- Beantwoord vragen in het Nederlands, tenzij anders gevraagd
- Baseer je antwoorden uitsluitend op de verstrekte context
- Citeer bronnen met [1], [2], etc. wanneer je informatie uit de context gebruikt
- Als de context onvoldoende informatie bevat, geef dit duidelijk aan
- Wees nauwkeurig en objectief bij het bespreken van veiligheidsvoorschriften

Context uit documenten:
{context}

Beantwoord de vraag van de gebruiker op basis van bovenstaande context."""


class Pipe:
    class Valves(BaseModel):
        # MCP Server Configuration
        MCP_SERVER_URL: str = Field(
            default="http://host.docker.internal:3434/mcp",
            description="URL of the genai-utils MCP server"
        )

        # LLM Configuration
        LLM_API_BASE_URL: str = Field(
            default="https://router.huggingface.co/v1",
            description="Base URL for the LLM API (OpenAI-compatible)"
        )
        LLM_API_KEY: str = Field(
            default="",
            description="API key for the LLM provider"
        )
        LLM_MODEL: str = Field(
            default="openai/gpt-oss-120b",
            description="Model ID to use for generation"
        )

        # Search Configuration
        DEFAULT_COLLECTION: str = Field(
            default="iaea",
            description="Default collection to search (anvs, iaea, wetten_overheid, security)"
        )
        MAX_CONTEXT_CHUNKS: int = Field(
            default=5,
            description="Maximum number of context chunks to include"
        )

        # Generation Configuration
        TEMPERATURE: float = Field(
            default=0.7,
            description="Temperature for LLM generation"
        )
        MAX_TOKENS: int = Field(
            default=2048,
            description="Maximum tokens in response"
        )

    def __init__(self):
        self.valves = self.Valves()

    async def _search_documents(self, query: str, collection: str) -> list:
        """Search documents via MCP server."""
        mcp_client = MCPClient()

        try:
            await mcp_client.connect(self.valves.MCP_SERVER_URL)

            result = await mcp_client.call_tool(
                "search_collection",
                {"query": query, "collection": collection}
            )

            return result if result else []

        except Exception as e:
            log.error(f"MCP search error: {e}")
            return []
        finally:
            try:
                await mcp_client.disconnect()
            except Exception:
                pass

    def _parse_mcp_result(self, result: list) -> tuple[str, list[dict]]:
        """
        Parse MCP result to extract text content and source metadata.

        MCP returns:
        - TextContent(type="text", text="...") - the retrieved chunks
        - EmbeddedResource(type="resource", resource={uri, text}) - JSON with sources

        Returns:
            Tuple of (context_text, sources_list)
        """
        context_text = ""
        sources = []

        for item in result:
            if not isinstance(item, dict):
                continue

            item_type = item.get("type")

            if item_type == "text":
                # Text content with the retrieved chunks
                context_text = item.get("text", "")

            elif item_type == "resource":
                # EmbeddedResource with file citations
                resource = item.get("resource", {})
                resource_text = resource.get("text", "")

                if resource_text:
                    try:
                        payload = json.loads(resource_text)
                        if payload.get("fileCitations") and "sources" in payload:
                            sources = payload["sources"]
                    except json.JSONDecodeError:
                        log.warning("Failed to parse MCP resource payload")

        return context_text, sources

    def _build_context_string(self, context_text: str) -> str:
        """Build context string, using MCP text or fallback message."""
        if not context_text or context_text.strip() == "":
            return "Geen relevante documenten gevonden."
        return context_text

    def _get_llm_config(self) -> tuple[str, str, str]:
        """Get LLM configuration from valves or environment variables."""
        # API key: valve > OPENAI_API_KEY env > empty
        api_key = self.valves.LLM_API_KEY or os.getenv("OPENAI_API_KEY", "")

        # Base URL: valve > OPENAI_API_BASE_URL env > default
        base_url = self.valves.LLM_API_BASE_URL
        if base_url == "https://router.huggingface.co/v1":  # default value
            base_url = os.getenv("OPENAI_API_BASE_URL", base_url)

        # Model: always use valve (user can configure)
        model = self.valves.LLM_MODEL

        return api_key, base_url, model

    async def _stream_llm_response(
        self,
        messages: list[dict],
    ) -> AsyncGenerator[str, None]:
        """Stream response from LLM API."""

        api_key, base_url, model = self._get_llm_config()

        # Check if API key is configured
        if not api_key:
            yield "Error: No API key found. Set LLM_API_KEY in Valves or OPENAI_API_KEY environment variable."
            return

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "temperature": self.valves.TEMPERATURE,
            "max_tokens": self.valves.MAX_TOKENS,
        }

        url = f"{base_url}/chat/completions"
        log.info(f"Calling LLM API: {url} with model {model}")

        timeout = aiohttp.ClientTimeout(total=120)  # 2 minute timeout
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    # Check if HTML response (likely auth redirect)
                    if "<html" in error_text.lower():
                        yield f"Error: LLM API returned {response.status}. Check that LLM_API_BASE_URL ({base_url}) and API key are correct."
                    else:
                        yield f"Error from LLM API ({response.status}): {error_text[:500]}"
                    return

                # Stream SSE response line by line
                async for line_bytes in response.content:
                    line = line_bytes.decode("utf-8").strip()

                    if not line or not line.startswith("data: "):
                        continue

                    data_str = line[6:]  # Remove "data: " prefix

                    if data_str == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)
                        delta = data.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue

    async def _emit_sources(self, sources: list[dict], __event_emitter__) -> None:
        """Emit source events for Open WebUI citation display."""
        if not __event_emitter__ or not sources:
            return

        # Limit to MAX_CONTEXT_CHUNKS
        sources = sources[:self.valves.MAX_CONTEXT_CHUNKS]

        for source in sources:
            # Extract source info from MCP format
            file_name = source.get("fileName", "Unknown Document")
            file_id = source.get("fileId", "")
            relevance = source.get("relevance", 0.75)
            metadata = source.get("metadata", {})
            url = metadata.get("url", "")

            # Build source event in Open WebUI format
            source_data = {
                "source": {
                    "id": file_id,
                    "name": file_name,
                    "url": url if url else None,
                },
                "document": [file_name],  # Document content preview
                "metadata": [{"source": url or file_id, "name": file_name}],
                "distances": [1 - relevance] if relevance else [],  # Convert relevance to distance
            }

            await __event_emitter__({
                "type": "source",
                "data": source_data,
            })

    # System tasks that should skip RAG and just pass through to LLM
    SYSTEM_TASKS = {
        "title_generation",
        "follow_up_generation",
        "tags_generation",
        "emoji_generation",
        "query_generation",
        "autocomplete_generation",
    }

    async def pipe(
        self,
        body: dict,
        __user__: dict = None,
        __task__: str = None,
        __event_emitter__=None,
    ) -> AsyncGenerator[str, None]:
        """Main pipe execution."""

        # Extract user message
        messages = body.get("messages", [])
        if not messages:
            yield "No messages provided."
            return

        user_message = messages[-1].get("content", "")

        # Skip MCP search for system tasks (follow-ups, title generation, etc.)
        # These don't need document retrieval - just pass through to LLM
        if __task__ and __task__ in self.SYSTEM_TASKS:
            log.info(f"[NEO NL Pipe] System task '{__task__}', skipping RAG")
            api_key, base_url, model = self._get_llm_config()
            if not api_key:
                yield ""
                return

            llm_messages = [{"role": "user", "content": user_message}]
            async for chunk in self._stream_llm_response(llm_messages):
                yield chunk
            return

        log.info(f"[NEO NL Pipe] Processing user query: {user_message[:100]}...")

        # Emit status: searching
        if __event_emitter__:
            await __event_emitter__({
                "type": "status",
                "data": {"description": "Zoeken in documenten...", "done": False}
            })

        # Search documents
        search_results = await self._search_documents(
            query=user_message,
            collection=self.valves.DEFAULT_COLLECTION
        )

        # Parse MCP result to get text and sources
        context_text, sources = self._parse_mcp_result(search_results)

        # Emit sources for citation display
        await self._emit_sources(sources, __event_emitter__)

        # Emit status: generating
        if __event_emitter__:
            await __event_emitter__({
                "type": "status",
                "data": {"description": f"Genereren van antwoord ({len(sources)} bronnen)...", "done": False}
            })

        # Build context and messages
        context_string = self._build_context_string(context_text)
        system_message = SYSTEM_PROMPT.format(context=context_string)

        llm_messages = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message}
        ]

        # Stream LLM response
        async for chunk in self._stream_llm_response(llm_messages):
            yield chunk

        # Emit status: done
        if __event_emitter__:
            await __event_emitter__({
                "type": "status",
                "data": {"description": "Voltooid", "done": True}
            })
