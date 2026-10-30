"""Les outils de Jarvis : ce qu'il peut consulter ou faire pendant une réponse.

Chaque outil a un nom, une description (lue par Claude pour décider quand
l'utiliser), un schéma JSON pour ses paramètres et une fonction Python.
Les paramètres viennent du modèle : ils sont validés avant toute exécution.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from ..audit import AuditLog
    from ..config import Settings
    from ..hub import DeviceHub
    from ..services import Services
    from ..session import DeviceSession

log = logging.getLogger(__name__)


class ToolError(Exception):
    """Erreur renvoyée au modèle (is_error=true), qui l'expliquera à l'utilisateur."""


@dataclass
class ToolContext:
    settings: Settings
    services: Services
    hub: DeviceHub
    session: DeviceSession
    audit: AuditLog | None = None


Handler = Callable[[dict, ToolContext], Awaitable[Any]]


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Handler
    status: str = ""  # message affiché pendant l'exécution (« Je consulte vos courriels… »)

    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            # Les paramètres arrivent au fil de l'eau : on les valide nous-mêmes (validate()).
            "eager_input_streaming": True,
        }


# ---------------------------------------------------------------- confirmation vocale

CONFIRM_SECONDS = 120


def ask_confirmation(ctx: ToolContext, action: str, summary: str, payload: object = None) -> dict:
    """Première étape d'une action sensible : rien n'est fait, on demande à l'utilisateur.

    `payload` (ex. le courriel exact à envoyer) est gardé côté serveur : ce qui sera exécuté après
    le « oui » est exactement ce qui a été présenté, le modèle ne peut plus le modifier."""
    code = secrets.token_hex(3)
    ctx.session.pending_confirmations[code] = (action, ctx.session.current_turn, time.monotonic(), payload)
    return {"status": "confirmation_requise", "confirmation_id": code,
            "consigne": f"Demande à l'utilisateur de confirmer : « {summary} ». N'exécute rien maintenant. "
                        "S'il répond oui dans sa PROCHAINE réponse, rappelle l'outil avec ce confirmation_id."}


def confirmed(ctx: ToolContext, code: str | None, action: str) -> bool:
    return confirmed_payload(ctx, code, action) is not _NOT_CONFIRMED


_NOT_CONFIRMED = object()


def confirmed_payload(ctx: ToolContext, code: str | None, action: str) -> object:
    """Renvoie le payload confirmé, ou _NOT_CONFIRMED si aucun code n'est fourni."""
    """Vrai seulement si l'utilisateur a repris la parole depuis la demande (imposé par le code,
    pas par le modèle : un courriel piégé ne peut pas « confirmer » à sa place)."""
    if not code:
        return _NOT_CONFIRMED
    entry = ctx.session.pending_confirmations.pop(code, None)
    if entry is None:
        raise ToolError("Confirmation inconnue ou expirée : redemande à l'utilisateur.")
    wanted, turn, created, payload = entry
    if wanted != action:
        raise ToolError("Cette confirmation concerne une autre action.")
    if time.monotonic() - created > CONFIRM_SECONDS:
        raise ToolError("Confirmation expirée (2 minutes) : redemande à l'utilisateur.")
    if ctx.session.current_turn <= turn:
        ctx.session.pending_confirmations[code] = entry
        raise ToolError("L'utilisateur n'a pas encore répondu : attends son « oui » dans sa prochaine réponse.")
    return payload


_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}


def validate(schema: dict, value: Any, path: str = "input") -> None:
    """Validation minimale d'un JSON Schema (types, required, propriétés inconnues, bornes)."""
    expected = schema.get("type")
    py = _TYPES.get(expected)
    if py and (not isinstance(value, py) or (expected in {"integer", "number"} and isinstance(value, bool))):
        raise ToolError(f"{path} : type {expected} attendu")
    if expected == "object":
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise ToolError(f"{path}.{key} : paramètre obligatoire manquant")
        for key, item in value.items():
            if key not in props:
                raise ToolError(f"{path}.{key} : paramètre inconnu")
            validate(props[key], item, f"{path}.{key}")
    elif expected == "array":
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ToolError(f"{path} : {schema['maxItems']} éléments maximum")
        for i, item in enumerate(value):
            validate(schema.get("items", {}), item, f"{path}[{i}]")
    elif expected in {"integer", "number"}:
        if "minimum" in schema and value < schema["minimum"]:
            raise ToolError(f"{path} : minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ToolError(f"{path} : maximum {schema['maximum']}")
    elif expected == "string":
        if "enum" in schema and value not in schema["enum"]:
            raise ToolError(f"{path} : valeur parmi {schema['enum']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ToolError(f"{path} : {schema['maxLength']} caractères maximum")


@dataclass
class ToolBox:
    """Les outils disponibles pour un tour de parole, liés à l'appareil qui parle."""

    tools: dict[str, Tool]
    context: ToolContext
    server_tools: list[dict] = field(default_factory=list)

    def definitions(self) -> list[dict]:
        # Ordre stable (trié) : le cache de prompt reste valide d'un tour à l'autre.
        return [self.tools[n].definition() for n in sorted(self.tools)] + self.server_tools

    async def run(self, tool_use_id: str, name: str, raw_input: Any) -> dict:
        tool = self.tools.get(name)
        try:
            if tool is None:
                raise ToolError(f"Outil inconnu : {name}")
            validate(tool.input_schema, raw_input)
            if tool.status:
                await self.context.session.send({"type": "status", "text": tool.status})
            result = await tool.handler(raw_input, self.context)
            self._audit(name, raw_input, "ok")
            if isinstance(result, (str, list)):  # liste = blocs de contenu (ex. une photo + du texte)
                content = result
            else:
                content = json.dumps(result, ensure_ascii=False, default=str)
            return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
        except ToolError as e:
            self._audit(name, raw_input, "refusé")
            return {"type": "tool_result", "tool_use_id": tool_use_id, "content": str(e), "is_error": True}
        except Exception as e:  # noqa: BLE001 - une panne d'outil ne doit pas faire tomber la réponse
            self._audit(name, raw_input, "erreur")
            log.exception("Outil %s en échec", name)
            return {"type": "tool_result", "tool_use_id": tool_use_id, "is_error": True,
                    "content": f"L'outil {name} a échoué ({type(e).__name__}). Réessaie plus tard."}

    def _audit(self, name: str, raw_input: Any, outcome: str) -> None:
        if self.context.audit is None:
            return
        from ..audit import summarize

        self.context.audit.record("tool", device=self.context.session.device, tool=name,
                                  args=summarize(raw_input if isinstance(raw_input, dict) else {}), outcome=outcome)
