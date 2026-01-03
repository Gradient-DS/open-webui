"""
title: NEO NL Document Assistant
description: Search nuclear safety documents via MCP and generate responses with citations
author: NEO NL Team
version: 0.8.0
"""

from pydantic import BaseModel, Field
from typing import AsyncGenerator
import json
import logging

from starlette.responses import StreamingResponse
from open_webui.utils.chat import generate_chat_completion
from open_webui.models.users import Users
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

        # LLM Configuration - uses Open WebUI's configured models
        LLM_MODEL: str = Field(
            default="",
            description="Model ID from Open WebUI (leave empty to use default model)"
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

    async def _call_llm(
        self,
        messages: list[dict],
        __user__: dict,
        __request__,
    ) -> AsyncGenerator[str, None]:
        """Call LLM using Open WebUI's internal routing."""

        if not __request__:
            yield "Error: Request context not available. Cannot call LLM."
            return

        if not __user__:
            yield "Error: User context not available. Cannot call LLM."
            return

        # Get user object for the API call
        user = Users.get_user_by_id(__user__.get("id"))
        if not user:
            yield "Error: User not found."
            return

        # Determine which model to use
        model_id = self.valves.LLM_MODEL
        if not model_id:
            # Use the first available non-pipe model from Open WebUI
            models = __request__.app.state.MODELS
            if models:
                # Filter out pipe models to avoid calling ourselves
                for mid, model in models.items():
                    if not model.get("pipe"):
                        model_id = mid
                        break
                if not model_id:
                    yield "Error: No non-pipe models configured in Open WebUI."
                    return
            else:
                yield "Error: No models configured in Open WebUI."
                return

        # Build the request payload
        payload = {
            "model": model_id,
            "messages": messages,
            "stream": True,
        }

        log.info(f"[NEO NL Pipe] Calling LLM via Open WebUI: model={model_id}")

        try:
            response = await generate_chat_completion(
                request=__request__,
                form_data=payload,
                user=user,
                bypass_filter=True,  # Skip access control for internal calls
            )

            # Handle streaming response
            if isinstance(response, StreamingResponse):
                async for chunk in response.body_iterator:
                    # Parse SSE format: "data: {...}\n\n"
                    chunk_str = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
                    for line in chunk_str.split("\n"):
                        line = line.strip()
                        if line.startswith("data: ") and line != "data: [DONE]":
                            try:
                                data = json.loads(line[6:])
                                content = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                if content:
                                    yield content
                            except json.JSONDecodeError:
                                continue
            elif isinstance(response, dict):
                # Non-streaming response
                content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
                yield content
            else:
                yield str(response)

        except Exception as e:
            log.error(f"[NEO NL Pipe] LLM call failed: {e}")
            yield f"Error calling LLM: {str(e)}"

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
        __request__=None,  # Required for generate_chat_completion
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
            llm_messages = [{"role": "user", "content": user_message}]
            async for chunk in self._call_llm(llm_messages, __user__, __request__):
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
        async for chunk in self._call_llm(llm_messages, __user__, __request__):
            yield chunk

        # Emit status: done
        if __event_emitter__:
            await __event_emitter__({
                "type": "status",
                "data": {"description": "Voltooid", "done": True}
            })
