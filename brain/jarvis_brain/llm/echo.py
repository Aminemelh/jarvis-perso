"""LLM factice pour tester la chaîne sans clé API ni coût."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator


class EchoLLM:
    async def stream(self, system: list[dict], messages: list[dict]) -> AsyncIterator[str]:
        question = messages[-1]["content"]
        reply = f"Bien reçu, Monsieur. Vous avez dit : {question}. Je suis en mode test, sans cerveau."
        for word in reply.split(" "):
            await asyncio.sleep(0.02)
            yield word + " "
