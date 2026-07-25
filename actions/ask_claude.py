"""Warm Claude Agent SDK bridge.

Lazily connects to a Claude Agent SDK session on first use and keeps the
session warm across calls (avoids paying reconnect cost on every question).
Only one question is processed at a time, serialized via an asyncio.Lock.

The session's working directory can be customized via a "claude_cwd" key in
config/api_keys.json; it defaults to the user's home directory.
"""

import asyncio
import json
import sys
from pathlib import Path


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


API_CONFIG_PATH = get_base_dir() / "config" / "api_keys.json"

SPOKEN_DISCIPLINE = """
Your answer is read aloud verbatim by a voice assistant. Write in short,
conversational sentences. Never use markdown, bullet points, code blocks,
headings, or emoji. Never spell out code or long file paths — describe them
in a short phrase instead. Lead with the direct answer first, then follow
with a few sentences of supporting detail. Reply in the same language as the
question.
""".strip()


class ClaudeBridge:
    def __init__(self):
        self._client = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _cwd() -> str:
        try:
            with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg.get("claude_cwd") or str(Path.home())
        except Exception:
            return str(Path.home())

    async def _connect(self):
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        options = ClaudeAgentOptions(
            cwd=self._cwd(),
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": SPOKEN_DISCIPLINE,
            },
            permission_mode="bypassPermissions",
        )
        self._client = ClaudeSDKClient(options=options)
        await self._client.__aenter__()
        print("[Claude] connected")

    async def ask(self, question: str) -> str:
        async with self._lock:
            if self._client is None:
                await self._connect()

            try:
                await self._client.query(question)

                texts = []
                async for msg in self._client.receive_response():
                    for block in getattr(msg, "content", None) or []:
                        text = getattr(block, "text", None)
                        if text:
                            texts.append(text)

                answer = "".join(texts).strip()
                return answer or "Claude returned no answer."

            except Exception:
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
                self._client = None
                raise

    async def close(self):
        try:
            if self._client is not None:
                await self._client.disconnect()
        except Exception:
            pass
        self._client = None


bridge = ClaudeBridge()
