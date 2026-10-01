"""Responses that are files, not JSON, documented as what they are (proposal T6.3).

FastAPI documents every response as JSON unless told otherwise, so a PDF report or
a JPEG frame would appear in the spec as JSON. These routes say what they return.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import Response


def files(description: str, *media_types: str) -> dict[str, Any]:
    """Route arguments for a 200 that is one of `media_types` ("*/*" for anything)."""
    return {
        "response_class": Response,
        "responses": {200: {"description": description, "content": {t: {} for t in media_types}}},
    }
