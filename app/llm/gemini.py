"""Gemini implementation of LLMClient (google-genai SDK). Model and key come from .env."""
from __future__ import annotations

import os
import time
from typing import Sequence

from google import genai
from google.genai import types

from app import config
from .base import LLMError, LLMResponse, Part, log_call, parse_json_text

_LEVELS = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH"}  # "minimal" is rejected by gemini-3.8-flash


class GeminiClient:
    def __init__(self, model: str | None = None, thinking_level: str | None = None,
                 timeout_seconds: float | None = None):
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise LLMError("GEMINI_API_KEY is missing (put it in .env)")
        self.model = model or config.GEMINI_MODEL
        level = (thinking_level or config.GEMINI_THINKING_LEVEL).lower()
        if level not in _LEVELS:
            raise LLMError(f"GEMINI_THINKING_LEVEL must be low, medium or high (got {level!r})")
        self._level = _LEVELS[level]
        self.provider = "gemini"
        self.cache_settings = {"thinking": self._level}          # part of the cache key: a different thinking level is a different answer
        timeout_ms = int((timeout_seconds or config.LLM_TIMEOUT_SECONDS) * 1000)
        self._client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(timeout=timeout_ms),
        )

    def generate_json(self, system: str, parts: Sequence[Part]) -> LLMResponse:
        contents = [
            p if isinstance(p, str) else types.Part.from_bytes(data=p.data, mime_type=p.mime_type)
            for p in parts
        ]
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_level=self._level),
        )
        started = time.monotonic()
        try:
            resp = self._client.models.generate_content(model=self.model, contents=contents, config=cfg)
        except Exception as ex:  # SDK raises several error types; the message never contains the key
            self._log({"outcome": "ERROR", "error": f"{type(ex).__name__}: {str(ex)[:300]}",
                       "seconds": round(time.monotonic() - started, 2)})
            raise LLMError(f"Gemini call failed: {type(ex).__name__}: {str(ex)[:300]}") from ex
        seconds = time.monotonic() - started
        text = (resp.text or "").strip()
        u = resp.usage_metadata
        usage = {
            "prompt_tokens": getattr(u, "prompt_token_count", 0) or 0,
            "output_tokens": getattr(u, "candidates_token_count", 0) or 0,
            "thought_tokens": getattr(u, "thoughts_token_count", 0) or 0,
        } if u else {}
        self._log({"outcome": "OK" if text else "EMPTY", "seconds": round(seconds, 2), "reply_chars": len(text), **usage})
        if not text:
            raise LLMError("Gemini returned an empty reply (blocked or out of tokens)")
        return LLMResponse(text=text, data=parse_json_text(text), model=self.model, seconds=seconds, usage=usage)

    def _log(self, entry: dict) -> None:
        log_call({"provider": "gemini", "model": self.model, "thinking": self._level, **entry})
