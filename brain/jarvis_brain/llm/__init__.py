"""Moteurs de langage. Tous exposent `stream(system, messages)` -> AsyncIterator[str]."""

from __future__ import annotations

from ..config import Settings
from .base import LLM


def create_llm(settings: Settings) -> LLM:
    if settings.llm_backend == "claude":
        from .claude import ClaudeLLM

        return ClaudeLLM(settings)
    if settings.llm_backend == "echo":
        from .echo import EchoLLM

        return EchoLLM()
    raise ValueError(f"JARVIS_LLM inconnu : {settings.llm_backend!r} (claude | echo)")
