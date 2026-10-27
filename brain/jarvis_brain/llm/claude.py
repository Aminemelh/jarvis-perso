"""Claude via l'API Anthropic, en streaming."""

from __future__ import annotations

import logging
from typing import AsyncIterator

import anthropic

from ..config import Settings

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    """Erreur présentable à l'utilisateur (le message est lu à voix haute)."""


class ClaudeLLM:
    def __init__(self, settings: Settings):
        self.settings = settings
        # La clé est lue dans ANTHROPIC_API_KEY (fichier .env), jamais dans le code.
        self.client = anthropic.AsyncAnthropic(max_retries=2, timeout=60.0)

    async def stream(self, system: list[dict], messages: list[dict]) -> AsyncIterator[str]:
        s = self.settings
        extra: dict = {}
        if s.claude_fallbacks:
            # Si le modèle refuse une requête (faux positif des filtres de sécurité),
            # l'API la relance automatiquement sur un modèle de secours.
            extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        try:
            async with self.client.beta.messages.stream(
                model=s.claude_model,
                max_tokens=s.claude_max_tokens,
                system=system,
                messages=messages,
                output_config={"effort": s.claude_effort},
                **extra,
            ) as stream:
                async for text in stream.text_stream:
                    yield text
                final = await stream.get_final_message()
            if final.stop_reason == "refusal":
                yield " Je crains de ne pas pouvoir répondre à cela, " + s.user_title + "."
            elif final.stop_reason == "max_tokens":
                log.warning("Réponse tronquée (max_tokens=%s)", s.claude_max_tokens)
            u = final.usage
            log.info(
                "Claude: %s tokens entrée (%s en cache), %s sortie",
                u.input_tokens, u.cache_read_input_tokens, u.output_tokens,
            )
        except anthropic.AuthenticationError as e:
            raise LLMError("Ma clé d'accès au cerveau est invalide. Vérifiez le fichier point env.") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Je suis momentanément saturé. Réessayez dans un instant.") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Je n'arrive pas à joindre mon cerveau. La connexion Internet semble coupée.") from e
        except anthropic.APIStatusError as e:
            log.error("Erreur API %s : %s", e.status_code, e.message)
            raise LLMError("Mon cerveau a rencontré une erreur. Réessayez dans un instant.") from e
