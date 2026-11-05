"""Offline check of the Domo client (a fake Domo service, no network): python tests/test_llm_domo.py  (or pytest)."""
import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile  # noqa: E402
os.environ.update(DOMO_INSTANCE="acme", DOMO_DEVELOPER_TOKEN="secret-token", CACHE_DIR=tempfile.mkdtemp(prefix="cache-test-"))

import httpx  # noqa: E402
from PIL import Image  # noqa: E402

from app.llm import FilePart, LLMError, make_client  # noqa: E402
from app.llm.base import parse_json_text  # noqa: E402
from app.llm.domo import DomoClient  # noqa: E402
from app import config  # noqa: E402

config.DOMO_INSTANCE = "acme"          # config may have been loaded by another test before the variables above were set

USAGE = {"inputTokens": 100, "outputTokens": 20, "reasoningTokens": 5}


def fake(reply, status=200):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"path": request.url.path, "host": request.url.host, "token": request.headers.get("X-DOMO-Developer-Token"),
                     "body": json.loads(request.content)})
        return httpx.Response(status, json=reply)
    return httpx.MockTransport(handler), seen


def png(side: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (side, side // 2), "white").save(buf, "PNG")
    return buf.getvalue()


def test_reader_call_uses_image_service():
    transport, seen = fake({"output": 'Here it is:\n```json\n{"items": [1]}\n```', "modelProviderUsage": USAGE, "modelId": "m1"})
    r = DomoClient(transport=transport).generate_json("SYS", [FilePart(png(100), "image/png"), "note"])
    assert r.data == {"items": [1]} and r.model == "m1"
    assert r.usage == {"prompt_tokens": 100, "output_tokens": 20, "thought_tokens": 5}
    req = seen[0]
    assert req["path"] == "/api/ai/v1/image/text" and req["host"] == "acme.domo.com" and req["token"] == "secret-token"
    assert req["body"]["input"] == "note" and req["body"]["system"].startswith("SYS") and req["body"]["image"]["mediaType"] == "image/png"
    assert req["body"]["reasoningConfig"] == {"reasoningEffort": "HIGH"}


def test_repair_call_uses_text_service_and_prose_around_json():
    transport, seen = fake({"output": 'Sure. {"status": "suggestion"} hope it helps', "modelProviderUsage": USAGE})
    assert DomoClient(transport=transport).generate_json("SYS", ["{}"]).data == {"status": "suggestion"}
    assert seen[0]["path"] == "/api/ai/v1/text/generation" and "image" not in seen[0]["body"]
    assert seen[0]["body"]["reasoningConfig"] == {"reasoningEffort": "HIGH"}


def test_reasoning_effort_changes_cache_keys_and_does_not_reuse_lower_effort_results():
    from app.cache import Cache
    from app.llm.cached import CachedClient
    cache = Cache(tempfile.mkdtemp(prefix="effort-cache-"))
    transport, seen = fake({"output": "{}"})
    low = CachedClient(DomoClient(transport=transport, reasoning_effort="low"), cache)
    high = CachedClient(DomoClient(transport=transport, reasoning_effort=" HIGH "), cache)
    assert low.key("SYS", ["input"]) != high.key("SYS", ["input"])
    low.generate_json("SYS", ["input"])
    high.generate_json("SYS", ["input"])
    assert len(seen) == 2
    assert [r["body"]["reasoningConfig"]["reasoningEffort"] for r in seen] == ["LOW", "HIGH"]
    assert high.generate_json("SYS", ["input"]).usage == {"cached": 1}
    assert len(seen) == 2


def test_invalid_reasoning_effort_is_refused_before_a_request():
    transport, seen = fake({"output": "{}"})
    try:
        DomoClient(transport=transport, reasoning_effort="unsupported")
    except LLMError as ex:
        assert "DOMO_REASONING_EFFORT" in str(ex) and not seen
    else:
        raise AssertionError("Invalid effort accepted")


def test_big_picture_is_shrunk_to_jpeg():
    transport, seen = fake({"output": "{}"})
    DomoClient(transport=transport).generate_json("S", [FilePart(png(4000), "image/png")])
    assert seen[0]["body"]["image"]["mediaType"] == "image/jpeg"


def test_errors_are_llm_errors_with_status_and_no_token():
    transport, _ = fake({"errorCode": "RATE", "message": "slow down"}, status=429)
    try:
        DomoClient(transport=transport).generate_json("S", ["x"])
        raise AssertionError("expected LLMError")
    except LLMError as ex:
        assert "429" in str(ex) and "secret-token" not in str(ex)      # "429" lets the orchestrator's automatic retry apply
    for parts in ([FilePart(b"%PDF", "application/pdf")], [FilePart(png(10), "image/png")] * 2):
        try:
            DomoClient(transport=fake({"output": "{}"})[0]).generate_json("S", parts)
            raise AssertionError("expected LLMError")
        except LLMError:
            pass


def test_calls_in_flight_are_limited():
    """Domo stalled with ~6 calls at once: however many threads call, at most DOMO_MAX_IN_FLIGHT requests are open."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from app.llm import domo as domo_mod
    state = {"now": 0, "max": 0}
    lock = threading.Lock()

    def slow(request):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.05)
        with lock:
            state["now"] -= 1
        return httpx.Response(200, json={"output": "{}"})
    client = DomoClient(transport=httpx.MockTransport(slow))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: client.generate_json("S", [str(i)]), range(8)))
    assert state["max"] == domo_mod._IN_FLIGHT._initial_value == config.DOMO_MAX_IN_FLIGHT, state


def test_make_client_switch():
    from app.llm.cached import CachedClient
    from app.llm.traced import TracedClient
    plain = make_client("domo", cache=False)                        # every client is traced (outermost)
    assert isinstance(plain, TracedClient) and isinstance(plain.inner, DomoClient)
    wrapped = make_client("domo")                                   # the cache is on by default: traced -> cached -> domo
    assert isinstance(wrapped.inner, CachedClient) and isinstance(wrapped.inner.inner, DomoClient)
    try:
        make_client("nope")
        raise AssertionError("expected LLMError")
    except LLMError:
        pass


def test_connection_reuse_isolated_by_endpoint_token_and_timeout():
    from unittest.mock import patch
    from app.llm import domo
    created=[]
    class Client:
        def __init__(self,**kwargs): self.closed=False;created.append(self)
        def close(self): self.closed=True
    # Replace the pool for this test without closing clients used by other tests.
    with patch.object(domo,"_HTTP_CLIENTS",{}),patch.object(domo.httpx,"Client",Client):
        first=domo._http_client("https://a.example","token-a",30)
        assert domo._http_client("https://a.example","token-a",30) is first
        assert domo._http_client("https://b.example","token-a",30) is not first
        assert domo._http_client("https://a.example","token-b",30) is not first
        assert domo._http_client("https://a.example","token-a",60) is not first
        assert len(created)==4
        domo._close_http_clients()
        assert all(client.closed for client in created) and not domo._HTTP_CLIENTS


def test_parse_json_text_still_strict_on_garbage():
    assert parse_json_text('```json\n[1, 2]\n```') == [1, 2]
    try:
        parse_json_text("no json here")
        raise AssertionError("expected LLMError")
    except LLMError:
        pass


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
