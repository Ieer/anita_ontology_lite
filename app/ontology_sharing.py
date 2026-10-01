"""Size-bounded URL-safe sharing for ontology JSON documents."""

from __future__ import annotations

import base64
import json
import re
import zlib
from typing import Any

from .ontology_documents import validate_document


MAX_DOCUMENT_BYTES = 32 * 1024
MAX_TOKEN_LENGTH = 50_000
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


def encode_document(document: dict[str, Any]) -> str:
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise ValueError("Ontology is too large to share in a URL.")
    if validate_document(document):
        raise ValueError("Ontology document must pass validation before sharing.")
    token = base64.urlsafe_b64encode(zlib.compress(payload, level=9)).decode("ascii").rstrip("=")
    if len(token) > MAX_TOKEN_LENGTH:
        raise ValueError("Share link exceeds the maximum length.")
    return token


def decode_document(token: str) -> dict[str, Any]:
    if not token or len(token) > MAX_TOKEN_LENGTH or not TOKEN_PATTERN.fullmatch(token):
        raise ValueError("Invalid or oversized share token.")
    try:
        compressed = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        decompressor = zlib.decompressobj()
        payload = decompressor.decompress(compressed, MAX_DOCUMENT_BYTES + 1)
        if len(payload) > MAX_DOCUMENT_BYTES or decompressor.unconsumed_tail:
            raise ValueError("Shared ontology exceeds the maximum document size.")
        payload += decompressor.flush()
        if len(payload) > MAX_DOCUMENT_BYTES or not decompressor.eof:
            raise ValueError("Invalid or oversized compressed share payload.")
        document = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, zlib.error) as error:
        if isinstance(error, ValueError) and str(error).startswith(("Shared ontology", "Invalid or oversized")):
            raise
        raise ValueError("Invalid share payload.") from error
    if not isinstance(document, dict) or validate_document(document):
        raise ValueError("Shared content is not a valid ontology document.")
    return document