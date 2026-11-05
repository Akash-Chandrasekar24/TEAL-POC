"""Cache layer C3: the same LLMClient interface, answers repeated calls from the cache.

key = sha256(CACHE_VERSION, provider, model, client settings, system prompt text, every part: text as is, a file as its sha256 + mime type)
- Only a successful call is stored (the inner client raised nothing, the reply is JSON). A reply the caller then finds invalid is removed
  with `discard(system, parts)` (the reader's format check, the repair guard), so a retry or a new job does real work again.
- A hit costs no tokens: its usage is {"cached": 1}, and the call is logged as CACHE_HIT.
- The human's correction note is part of the prompt, so a corrected call never hits the uncorrected reply.
"""
from __future__ import annotations

import time
import threading
from typing import Sequence

from app.cache import Cache, key as make_key
from .base import LLMClient, LLMResponse, Part, log_call

LAYER = "llm"
CACHE_VERSION = 1          # bump when a client changes how it turns (system, parts) into a request (e.g. Domo's JSON-only suffix)
_IN_PROGRESS = [threading.Lock() for _ in range(128)]


class CachedClient:
    def __init__(self, inner: LLMClient, cache: Cache, tenant: str = "default"):
        self.inner, self.cache, self.tenant = inner, cache, tenant
        self.model = inner.model
        self.provider = getattr(inner, "provider", type(inner).__name__)
        self.cache_settings = getattr(inner, "cache_settings", {})

    def key(self, system: str, parts: Sequence[Part]) -> str:
        norm = [p if isinstance(p, str) else {"file": make_key(p.data), "mime": p.mime_type} for p in parts]
        return make_key(CACHE_VERSION, self.provider, self.inner.model, getattr(self.inner, "cache_settings", {}), system, norm)

    def generate_json(self, system: str, parts: Sequence[Part]) -> LLMResponse:
        k = self.key(system, parts)
        # Recheck the cache after waiting: simultaneous repeated uploads share
        # one successful provider request rather than spending a slot each.
        with _IN_PROGRESS[int(k[:8], 16) % len(_IN_PROGRESS)]:
            return self._generate_json(k, system, parts)

    def _generate_json(self, k, system, parts):
        hit = self.cache.get_json(self.tenant, LAYER, k)
        if hit is not None:
            log_call({"provider": self.provider, "model": hit["model"], "outcome": "CACHE_HIT", "key": k[:16]})
            return LLMResponse(text=hit["text"], data=hit["data"], model=hit["model"], seconds=0.0, usage={"cached": 1})
        started = time.monotonic()
        resp = self.inner.generate_json(system, parts)                 # raises on failure: nothing is stored
        self.cache.put_json(self.tenant, LAYER, k, {"text": resp.text, "data": resp.data, "model": resp.model, "usage": resp.usage,
                                                     "seconds": round(time.monotonic() - started, 2)},
                            versions={"cache": CACHE_VERSION, "provider": self.provider, "model": resp.model})
        return resp

    def discard(self, system: str, parts: Sequence[Part]) -> None:
        """Forget the stored reply of this exact call (the caller found it invalid)."""
        self.cache.discard(self.tenant, LAYER, self.key(system, parts))
