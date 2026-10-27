from __future__ import annotations

from typing import AsyncIterator, Protocol


class LLM(Protocol):
    def stream(self, system: list[dict], messages: list[dict]) -> AsyncIterator[str]:
        """Produit la réponse morceau par morceau."""
        ...
