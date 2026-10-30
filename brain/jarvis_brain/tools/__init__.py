"""Les outils de Jarvis : ce qu'il peut consulter ou faire pendant une réponse.

Chaque outil a un nom, une description (lue par Claude pour décider quand
l'utiliser), un schéma JSON pour ses paramètres et une fonction Python.
Les paramètres viennent du modèle : ils sont validés avant toute exécution.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
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
            content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
            return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
        except ToolError as e:
            return {"type": "tool_result", "tool_use_id": tool_use_id, "content": str(e), "is_error": True}
        except Exception as e:  # noqa: BLE001 - une panne d'outil ne doit pas faire tomber la réponse
            log.exception("Outil %s en échec", name)
            return {"type": "tool_result", "tool_use_id": tool_use_id, "is_error": True,
                    "content": f"L'outil {name} a échoué ({type(e).__name__}). Réessaie plus tard."}
