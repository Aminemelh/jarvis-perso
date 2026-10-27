from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator, Protocol

if TYPE_CHECKING:
    from ..tools import ToolBox


class LLM(Protocol):
    def stream(self, system: list[dict], messages: list[dict],
               toolbox: ToolBox | None = None) -> AsyncIterator[str]:
        """Produit la réponse morceau par morceau, en appelant les outils au besoin."""
        ...
