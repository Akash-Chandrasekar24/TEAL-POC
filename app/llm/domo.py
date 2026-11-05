"""Domo AI Service Layer implementation of LLMClient (ported from the Tanishq POC's app/domo.py, cut down to what this project needs).

Domo has two REST services (same auth, same reply shape):
  POST /api/ai/v1/text/generation   {model, input, system}          -> the repair step (text only)
  POST /api/ai/v1/image/text        {model, input, system, image}   -> the reader step (ONE drawing + text)
There is no JSON mode, so the system prompt asks for JSON only and parse_json_text pulls the object out of the reply.
Settings come from .env: DOMO_INSTANCE, DOMO_DEVELOPER_TOKEN, DOMO_MODEL and DOMO_REASONING_EFFORT.
Both text and image calls send the documented reasoningConfig.reasoningEffort.
"""
from __future__ import annotations

import base64
import atexit
import hashlib
import io
import threading
import time
from typing import Sequence

import httpx

from app import config
from .base import FilePart, LLMError, LLMResponse, Part, log_call, parse_json_text

TEXT_PATH = "/api/ai/v1/text/generation"
IMAGE_PATH = "/api/ai/v1/image/text"
MAX_IMAGE_BYTES = 3_500_000          # Domo refuses an image over 3.75 MB
MAX_IMAGE_SIDE = 2048
_DOMO_MIMES = ("image/png", "image/jpeg", "image/webp")
JSON_ONLY = "\n\nReply with only one JSON value: no prose, no code fence."
_EFFORTS = {"none", "low", "medium", "high", "xhigh", "max"}
# ponytail: one limit per process; jobs in several processes each get their own, so a shared queue is needed once the system runs on several workers
_IN_FLIGHT = threading.BoundedSemaphore(config.DOMO_MAX_IN_FLIGHT)
_HTTP_LOCK = threading.Lock()
_HTTP_CLIENTS = {}


def _http_client(base, token, timeout):
    """Reuse transport/keep-alive connections, never model answers or run state."""
    key=(base,hashlib.sha256(token.encode()).digest(),timeout)
    with _HTTP_LOCK:
        client=_HTTP_CLIENTS.get(key)
        if client is None:
            client=httpx.Client(headers={"X-DOMO-Developer-Token":token},timeout=timeout)
            _HTTP_CLIENTS[key]=client
        return client


def _close_http_clients():
    with _HTTP_LOCK:
        for client in _HTTP_CLIENTS.values(): client.close()
        _HTTP_CLIENTS.clear()


atexit.register(_close_http_clients)


class DomoClient:
    def __init__(self, model: str | None = None, timeout_seconds: float | None = None, transport: httpx.BaseTransport | None = None,
                 reasoning_effort: str | None = None):
        host = config.DOMO_INSTANCE.removeprefix("https://").removeprefix("http://").rstrip("/")
        token = config.domo_token()
        if not (host and token):
            raise LLMError("Domo is not configured: set DOMO_INSTANCE and DOMO_DEVELOPER_TOKEN in .env")
        self.model = model or config.DOMO_MODEL
        self.provider = "domo"
        self.reasoning_effort = (reasoning_effort if reasoning_effort is not None else config.DOMO_REASONING_EFFORT).strip().lower()
        if self.reasoning_effort not in _EFFORTS:
            raise LLMError("DOMO_REASONING_EFFORT must be none, low, medium, high, xhigh or max")
        self.cache_settings = {"reasoning_effort": self.reasoning_effort}
        self._base = f"https://{host if '.' in host else host + '.domo.com'}"
        timeout=timeout_seconds or config.LLM_TIMEOUT_SECONDS
        self._http = (httpx.Client(headers={"X-DOMO-Developer-Token":token},timeout=timeout,transport=transport)
                      if transport is not None else _http_client(self._base,token,timeout))

    def generate_json(self, system: str, parts: Sequence[Part]) -> LLMResponse:
        files = [p for p in parts if isinstance(p, FilePart)]
        if len(files) > 1:
            raise LLMError("Domo takes one picture per call")
        body: dict = {"model": self.model, "input": "\n\n".join(p for p in parts if isinstance(p, str)) or "Read the attached drawing.",
                      "system": system + JSON_ONLY,
                      "reasoningConfig": {"reasoningEffort": self.reasoning_effort.upper()}}
        path = TEXT_PATH
        if files:
            data, mime = _prepare_image(files[0])
            body["image"] = {"type": "base64", "mediaType": mime, "data": base64.b64encode(data).decode()}
            path = IMAGE_PATH
        started = time.monotonic()
        try:
            raw = self._post(path, body)
        except LLMError as ex:
            log_call({"provider": "domo", "model": self.model, "outcome": "ERROR", "error": str(ex), "seconds": round(time.monotonic() - started, 2)})
            raise
        seconds = time.monotonic() - started
        text = (raw.get("output") or "").strip() if isinstance(raw.get("output"), str) else ""
        u = raw.get("modelProviderUsage") or {}
        usage = {"prompt_tokens": u.get("inputTokens") or 0, "output_tokens": u.get("outputTokens") or 0, "thought_tokens": u.get("reasoningTokens") or 0}
        log_call({"provider": "domo", "model": self.model, "outcome": "OK" if text else "EMPTY", "seconds": round(seconds, 2), "reply_chars": len(text), **usage})
        if not text:
            raise LLMError("Domo returned an empty reply (out of tokens?)")
        return LLMResponse(text=text, data=parse_json_text(text), model=raw.get("modelId") or self.model, seconds=seconds, usage=usage)

    def _post(self, path: str, body: dict) -> dict:
        """One request. Errors name the HTTP status (503, 429...) so the orchestrator's one automatic retry of a temporary error applies."""
        try:
            with _IN_FLIGHT:                                                  # Domo stalls (10 min, measured) with ~6 calls in flight
                r = self._http.post(self._base + path, json=body)
        except httpx.TimeoutException:
            raise LLMError("Domo call timed out") from None
        except httpx.HTTPError as ex:
            raise LLMError(f"Domo call failed: {type(ex).__name__}") from None
        if r.status_code >= 400:
            try:
                err = r.json()
            except ValueError:
                err = {}
            msg = (err.get("localizedMessage") or err.get("message")) if isinstance(err, dict) else None
            code = err.get("errorCode") if isinstance(err, dict) else None
            raise LLMError(f"Domo {r.status_code}{' ' + code if code else ''}: {msg or r.reason_phrase}")
        try:
            return r.json()
        except ValueError:
            raise LLMError("Domo returned a reply that is not JSON") from None


def _prepare_image(part: FilePart) -> tuple[bytes, str]:
    """(bytes, mime) Domo accepts: PNG / JPEG / WebP as they are when small enough, else a smaller JPEG. A PDF is refused (Domo reads pictures only)."""
    if part.mime_type == "application/pdf":
        raise LLMError("Domo cannot read a PDF: export the drawing page as PNG and start the job with that, or use --llm gemini")
    from PIL import Image                                       # Pillow comes with matplotlib
    try:
        im = Image.open(io.BytesIO(part.data))
        if part.mime_type in _DOMO_MIMES and len(part.data) <= MAX_IMAGE_BYTES and max(im.size) <= MAX_IMAGE_SIDE:
            return part.data, part.mime_type
        im = im.convert("RGB")
    except OSError:
        raise LLMError("the drawing cannot be read as a picture") from None
    # ponytail: a drawing over 2048 px is shrunk to fit Domo's limit, so tiny dimension text can blur; send a tighter crop if a read misses numbers
    before = im.size
    im.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    from app import trace
    trace.current().warn("llm", "picture shrunk for Domo (small text may blur)", original=list(before), sent=list(im.size),
                         original_bytes=len(part.data), limit_px=MAX_IMAGE_SIDE)
    for quality in (92, 80, 65):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality)
        if buf.tell() <= MAX_IMAGE_BYTES:
            break
    return buf.getvalue(), "image/jpeg"
