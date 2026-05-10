"""QR code generation for short links.

Generates PNG QR codes encoding the short URL. Only served when
explicitly requested via /{alias}/qr endpoint.

Sig: 2026-05-08 created
"""

from __future__ import annotations

import io

import qrcode
from fastapi import HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from PIL import Image

try:
    from generated.models import ShortLink
except ImportError:
    from models import ShortLink


async def generate_qr(
    alias: str,
    repo,
    request: Request = None,
    size: int = Query(256),
) -> StreamingResponse:
    """Generate a PNG QR code encoding the short URL for ``alias``.

    Args:
        alias: Short link identifier to encode in the QR code.
        repo: Repository providing async ``get`` and ``update`` on ShortLink.
        request: Incoming FastAPI request, used to derive the base URL.
        size: Output image edge length in pixels. Defaults to 256.

    Returns:
        StreamingResponse streaming the rendered PNG bytes with
        ``media_type="image/png"``.

    Raises:
        HTTPException: 404 when the alias is not registered in storage.

    Sig: 2026-05-08 created
    """
    link = await repo.get(ShortLink, alias)
    if link is None:
        raise HTTPException(status_code=404, detail="Short link not found")

    base = str(request.base_url) if request else "http://localhost:8000/"
    short_url = f"{base}{alias}"

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(short_url)
    qr.make(fit=True)

    image = qr.make_image(fill_color="black", back_color="white")
    # qrcode returns a PilImage wrapper; normalise to a PIL.Image for resizing.
    pil_image = image.get_image() if hasattr(image, "get_image") else image
    pil_image = pil_image.resize((size, size), Image.NEAREST)

    buffer = io.BytesIO()
    pil_image.save(buffer, format="PNG")
    buffer.seek(0)

    return StreamingResponse(buffer, media_type="image/png")
