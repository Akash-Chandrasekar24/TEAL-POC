from .base import FilePart, LLMClient, LLMError, LLMJsonError, LLMResponse, Part, parse_json_text

PROVIDERS = ("gemini", "domo")


def make_client(provider: str | None = None, cache: bool | None = None, *, reasoning_effort: str | None = None) -> LLMClient:
    """The model client for `provider` (gemini | domo); None = LLM_PROVIDER from .env (default domo).
    `cache`: answer repeated calls from the cache (layer C3); None = LLM_CACHE from .env (default on)."""
    from app import config
    name = (provider or config.LLM_PROVIDER).strip().lower()
    if name == "domo":
        from .domo import DomoClient
        client: LLMClient = DomoClient(reasoning_effort=reasoning_effort)
    elif name == "gemini":
        from .gemini import GeminiClient
        client = GeminiClient(thinking_level=reasoning_effort)
    else:
        raise LLMError(f"unknown LLM provider {name!r}: use one of {', '.join(PROVIDERS)}")
    if config.LLM_CACHE if cache is None else cache:
        from app.cache import default_cache
        from .cached import CachedClient
        client = CachedClient(client, default_cache(), config.TENANT)
    from .traced import TracedClient
    return TracedClient(client)                                  # outermost: every call (cached or not) goes into the run's trace


__all__ = ["FilePart", "LLMClient", "LLMError", "LLMJsonError", "LLMResponse", "PROVIDERS", "Part", "make_client", "parse_json_text"]
