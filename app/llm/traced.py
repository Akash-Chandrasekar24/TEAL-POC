"""Every model call into the run's trace (app.trace): what was asked, what was shown, what came back, what it cost. Outermost client layer."""
from __future__ import annotations

import hashlib
import io
import time
import threading
from typing import Sequence

from app import trace
from .base import FilePart, LLMClient, LLMError, LLMResponse, Part


def _image_info(p: FilePart) -> dict:
    info = {"mime": p.mime_type, "bytes": len(p.data)}
    try:
        from PIL import Image
        info["size"] = list(Image.open(io.BytesIO(p.data)).size)
    except Exception:                                                     # a PDF or an unreadable picture: size unknown, still logged
        pass
    return info


class TracedClient:
    def __init__(self, inner: LLMClient):
        self.inner, self.model = inner, inner.model
        self.provider = getattr(inner, "provider", type(inner).__name__)
        self.cache_settings = getattr(inner, "cache_settings", {})
        self._n = 0
        self._lock = threading.Lock()

    def generate_json(self, system: str, parts: Sequence[Part]) -> LLMResponse:
        tr = trace.current()
        with self._lock:
            self._n += 1
            call_id = self._n
        prompt = {"prompt": system.split(".")[0][:70], "prompt_sha": hashlib.sha256(system.encode()).hexdigest()[:10], "prompt_chars": len(system)}
        shown = []
        for i, p in enumerate(parts):
            if isinstance(p, str):
                shown.append({"text_chars": len(p), "text": p[:200]})
            else:
                info = _image_info(p)
                ext = "pdf" if p.mime_type == "application/pdf" else p.mime_type.split("/")[-1]
                info["file"] = tr.save(f"call{call_id}_input{i}.{ext}", p.data)
                shown.append(info)
        tr.event("llm", "call", provider=self.provider, model=self.model, settings=self.cache_settings, **prompt, parts=shown)
        t0 = time.monotonic()
        try:
            resp = self.inner.generate_json(system, parts)
        except LLMError as ex:
            tr.event("llm", "call failed", level="error", seconds=round(time.monotonic() - t0, 2), error=str(ex), **prompt)
            raise
        reply_file = tr.save(f"call{call_id}_reply.json", resp.text)
        tr.event("llm", "reply", seconds=round(time.monotonic() - t0, 2), cached=bool(resp.usage.get("cached")), model=resp.model,
                 tokens_in=resp.usage.get("prompt_tokens", 0), tokens_out=resp.usage.get("output_tokens", 0),
                 tokens_thought=resp.usage.get("thought_tokens", 0), reply_chars=len(resp.text), reply_file=reply_file, **prompt)
        return resp

    def discard(self, system: str, parts: Sequence[Part]) -> None:
        trace.current().warn("llm", "reply discarded from the cache (the caller found it invalid)")
        getattr(self.inner, "discard", lambda *a: None)(system, parts)
