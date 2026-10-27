"""Claude via l'API Anthropic, en streaming, avec boucle d'outils."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, AsyncIterator

import anthropic

from ..config import Settings

if TYPE_CHECKING:
    from ..tools import ToolBox

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_ROUNDS = 8  # appels d'outils successifs maximum pour une seule question
MAX_JSON_RETRIES = 2


class LLMError(RuntimeError):
    """Erreur présentable à l'utilisateur (le message est lu à voix haute)."""


class ClaudeLLM:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None):
        self.settings = settings
        # La clé est lue dans ANTHROPIC_API_KEY (fichier .env), jamais dans le code.
        self.client = client or anthropic.AsyncAnthropic(max_retries=2, timeout=60.0)

    async def stream(self, system: list[dict], messages: list[dict],
                     toolbox: ToolBox | None = None) -> AsyncIterator[str]:
        s = self.settings
        params: dict = {
            "model": s.claude_model,
            "max_tokens": s.claude_max_tokens,
            "system": system,
            "output_config": {"effort": s.claude_effort},
        }
        if s.claude_fallbacks:
            # Si le modèle refuse une requête (faux positif des filtres de sécurité),
            # l'API la relance automatiquement sur un modèle de secours.
            params |= {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        if toolbox:
            params["tools"] = toolbox.definitions()

        # Historique de CE tour, en ajout seulement : les blocs renvoyés par l'API
        # (réflexion comprise) repartent tels quels à chaque appel d'outil.
        msgs = list(messages)
        json_retries = 0
        spoke = False
        try:
            for _ in range(MAX_ROUNDS):
                separate = spoke  # un espace entre le texte d'avant et d'après un outil
                try:
                    async with self.client.beta.messages.stream(messages=msgs, **params) as stream:
                        async for text in stream.text_stream:
                            if separate:
                                yield " "
                                separate = False
                            spoke = True
                            yield text
                        final = await stream.get_final_message()
                    json_retries = 0
                except ValueError:
                    # Paramètres d'outil en JSON illisible : on relance l'appel (borné).
                    json_retries += 1
                    if json_retries > MAX_JSON_RETRIES:
                        raise LLMError("Je me suis embrouillé dans mes outils. Pouvez-vous reformuler ?")
                    continue

                self._log_usage(final)
                if final.stop_reason == "refusal":
                    yield f" Je crains de ne pas pouvoir répondre à cela, {s.user_title}."
                    return
                if final.stop_reason == "pause_turn":  # recherche web longue : on laisse reprendre
                    msgs.append({"role": "assistant", "content": final.content})
                    continue
                tool_uses = [b for b in final.content if b.type == "tool_use"]
                if not tool_uses or toolbox is None:
                    if final.stop_reason == "max_tokens":
                        log.warning("Réponse tronquée (max_tokens=%s)", s.claude_max_tokens)
                    return
                if final.stop_reason == "max_tokens":
                    # Paramètres d'outil tronqués : on ne les exécute pas.
                    log.warning("Appel d'outil tronqué (max_tokens=%s)", s.claude_max_tokens)
                    yield " Désolé, ma réponse a été coupée."
                    return
                results = await asyncio.gather(*(toolbox.run(b.id, b.name, b.input) for b in tool_uses))
                msgs.append({"role": "assistant", "content": final.content})
                msgs.append({"role": "user", "content": list(results)})
            yield " Je n'ai pas réussi à terminer cette recherche."
        except anthropic.AuthenticationError as e:
            raise LLMError("Ma clé d'accès au cerveau est invalide. Vérifiez le fichier point env.") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Je suis momentanément saturé. Réessayez dans un instant.") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Je n'arrive pas à joindre mon cerveau. La connexion Internet semble coupée.") from e
        except anthropic.APIStatusError as e:
            log.error("Erreur API %s : %s", e.status_code, e.message)
            raise LLMError("Mon cerveau a rencontré une erreur. Réessayez dans un instant.") from e

    @staticmethod
    def _log_usage(final) -> None:
        u = final.usage
        log.info("Claude: %s tokens entrée (%s en cache), %s sortie, arrêt=%s",
                 u.input_tokens, u.cache_read_input_tokens, u.output_tokens, final.stop_reason)
