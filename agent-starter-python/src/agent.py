import logging
import os
from typing import Optional
from urllib.parse import quote
import aiohttp
import asyncio
import handlebars
import json
import uuid
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    ToolError,
    cli,
    function_tool,
    inference,
    room_io,
    utils,
)
from livekit.plugins import (
    noise_cancellation,
    silero,
)
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("agent-Emery-2af")

load_dotenv(".env.local")


class VariableTemplater:
    def __init__(self, metadata: str, additional: dict[str, dict[str, str]] | None = None) -> None:
        self.variables = {
            "metadata": self._parse_metadata(metadata),
        }
        if additional:
            self.variables.update(additional)
        self._cache = {}
        self._compiler = handlebars.Compiler()

    def _parse_metadata(self, metadata: str) -> dict:
        try:
            value = json.loads(metadata)
            if isinstance(value, dict):
                return value
            else:
                logger.warning(f"Job metadata is not a JSON dict: {metadata}")
                return {}
        except json.JSONDecodeError:
            return {}

    def _compile(self, template: str):
        if template in self._cache:
            return self._cache[template]
        self._cache[template] = self._compiler.compile(template)
        return self._cache[template]

    def render(self, template: str):
        return self._compile(template)(self.variables)


class DefaultAgent(Agent):
    def __init__(self, metadata: str, fallback_conversation_id: Optional[str] = None) -> None:
        self._conversation_id = fallback_conversation_id or str(uuid.uuid4())
        self._templater = VariableTemplater(metadata)
        self._headers_templater = VariableTemplater(metadata, {"secrets": dict(os.environ)})
        super().__init__(
            instructions=self._templater.render("""You are tasked with answering customer product queries using the information retrieved from the attached hybrid search webhook tool.

When answering a question, if the customer question requires contextual product data, use the hybrid webhook search tool to retrieve relevant product rows.

The hybrid search tool combines both full-text search and semantic search to retrieve the 5 most relevant product rows. Only use the returned rows when formulating your response.

IMPORTANT LIMITATION:
The tool does NOT return the full product catalogue. It only returns up to 5 relevant matching rows. Therefore, do not assume the returned rows represent all available products.

If a customer asks a question that requires complete catalogue knowledge, exhaustive comparison, or all matching products, you must refuse and say:
“Sorry I don’t know. Feel free to visit the office and ask one of our staff members”

Examples of questions you must refuse:
- How many products do you have in this category?
- What is the cheapest product you sell?
- What is the most expensive product you sell?
- Show me all products in this category
- What are all brands you stock?
- Which product sells the most overall?
- What is your full price range for this category?

You may answer comparative questions ONLY when the comparison can be made solely from the returned rows and you make it clear the answer is based on the retrieved results, not the full catalogue.

All column names are self-explanatory apart from the 'sales' column which you can interpret as the product's price in EURO.

The 'description' column values are messy, so you may ignore strange symbols, expand abbreviations, and rewrite descriptions naturally.

Do not reproduce description fields verbatim. Instead, explain the product clearly and naturally like a knowledgeable sales assistant.

Your goal is to provide an accurate answer specific to what the customer asked based ONLY on the retrieved information.

If you cannot answer the question using the provided information, if no rows are returned, or if the question requires complete catalogue knowledge beyond the retrieved rows, say:

“Sorry I don’t know. Feel free to visit the office and ask one of our staff members”."""),
        )

    async def on_enter(self):
        await self.session.generate_reply(
            instructions=self._templater.render("""Hello! Welcome to Fitzgerald Flowers! How can I help you today?"""),
            allow_interruptions=True,
        )

    @function_tool(name="fitzgerald_flowers_product_hybrid_search")
    async def _http_tool_fitzgerald_flowers_product_hybrid_search(
        self, context: RunContext, query: str
    ) -> str | None:
        """
        Makes an HTTP request to return the most relevant products based on a combination of full-text and semantic search.

        Args:
            query: Parameter decided by you in order to return the most relevant product rows related to the customer's prompt.
        """

        url = "https://favfzwgqlyupldmppocr.supabase.co/functions/v1/cached-hybrid-search"
        headers = {
            "Authorization": self._headers_templater.render("Bearer {{secrets.SUPABASE_SERVICE_ROLE_KEY}}"),
            "Conversation-Id": self._conversation_id,
        }
        payload = {
            "query": query,
        }

        try:
            session = utils.http_context.http_session()
            timeout = aiohttp.ClientTimeout(total=10)
            async with session.post(url, timeout=timeout, headers=headers, json=payload) as resp:
                if resp.status >= 400:
                    raise ToolError(f"error: HTTP {resp.status}")
                return await resp.text()
        except ToolError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise ToolError(f"error: {e!s}") from e
        """
            try:
                session = utils.http_context.http_session()
                async with session.post(
                    url, timeout=timeout, headers=headers, json=payload
                ) as resp:
                    if resp.status >= 400:
                        raise ToolError(f"error: HTTP {resp.status}")
                    return await resp.text()
            except RuntimeError:
                # `utils.http_context.http_session()` is only available when running inside
                # the LiveKit worker "job context". For local eval runs (standalone
                # AgentSession), fall back to a temporary aiohttp session.
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(url, headers=headers, json=payload) as resp:
                        if resp.status >= 400:
                            raise ToolError(f"error: HTTP {resp.status}")
                        return await resp.text()
        """

server = AgentServer()

def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()

server.setup_fnc = prewarm

@server.rtc_session()
async def entrypoint(ctx: JobContext):
    runtime_conversation_id = (
        getattr(ctx.room, "name", None)
        or getattr(ctx.job, "id", None)
    )

    session = AgentSession(
        stt=inference.STT(model="cartesia/ink-whisper", language="en"),
        llm=inference.LLM(model="openai/gpt-4o-mini"),
        tts=inference.TTS(
            model="cartesia/sonic-3",
            voice="a167e0f3-df7e-4d52-a9c3-f949145efdab",
            language="en"
        ),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    await session.start(
        agent=DefaultAgent(
            metadata=ctx.job.metadata,
            fallback_conversation_id=runtime_conversation_id,
        ),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda params: noise_cancellation.BVCTelephony() if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP else noise_cancellation.BVC(),
            ),
        ),
    )

    # Join the room and connect to the user
    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(server)
