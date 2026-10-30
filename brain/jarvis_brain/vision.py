"""Photos envoyées par les appareils (caméra du téléphone ou du PC), pour la vision de Claude."""

from __future__ import annotations

import base64
import binascii

from .sources.images import sniff_image

MAX_PHOTO_BYTES = 3 * 1024 * 1024
ALLOWED = {"image/jpeg", "image/png", "image/webp"}


class PhotoError(ValueError):
    pass


def decode_photo(data: str) -> tuple[bytes, str]:
    """Valide une photo reçue en base64 : taille, et format reconnu à partir des octets."""
    if not isinstance(data, str) or len(data) > MAX_PHOTO_BYTES * 4 // 3 + 16:
        raise PhotoError("Photo trop volumineuse (3 Mo maximum).")
    if data.startswith("data:"):
        data = data.partition(",")[2]
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as e:
        raise PhotoError("Photo illisible.") from e
    mime = sniff_image(raw)
    if mime not in ALLOWED:
        raise PhotoError("Format de photo non pris en charge (JPEG, PNG ou WebP).")
    return raw, mime


def image_block(raw: bytes, mime: str) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": mime,
                                        "data": base64.b64encode(raw).decode("ascii")}}
