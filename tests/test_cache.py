"""Offline checks of the Phase 1 cache (blob store + index + CachedClient): python tests/test_cache.py  (or pytest)."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["CACHE_DIR"] = tempfile.mkdtemp(prefix="cache-test-")          # never touch the project's storage/

from app.cache import Cache, key  # noqa: E402
from app.llm.base import FilePart, LLMError, LLMResponse  # noqa: E402
from app.llm.cached import CachedClient  # noqa: E402


def new_cache(**kw) -> Cache:
    return Cache(tempfile.mkdtemp(prefix="cache-test-"), **kw)


class FakeLLM:
    """Counts real calls; replies {"n": call number}; `fail` makes the next call raise."""
    provider, model, cache_settings = "fake", "fake-1", {"thinking": "MEDIUM"}

    def __init__(self):
        self.calls, self.fail = 0, False

    def generate_json(self, system, parts):
        if self.fail:
            raise LLMError("Fake 503: busy")
        self.calls += 1
        return LLMResponse(text=f'{{"n": {self.calls}}}', data={"n": self.calls}, model=self.model, seconds=1.0, usage={"prompt_tokens": 10})


def test_blobs_are_content_addressed_and_shared():
    c = new_cache()
    a, b = c.put_blob(b"same"), c.put_blob(b"same")
    assert a == b and c.get_blob(a) == b"same"
    c._blob_path(a).write_bytes(b"damaged")
    assert c.get_blob(a) is None                                      # a damaged blob is a miss, never wrong data


def test_get_put_tenant_isolation_and_purge():
    c = new_cache()
    c.put_json("acme", "llm", "k1", {"v": 1})
    assert c.get_json("acme", "llm", "k1") == {"v": 1}
    assert c.get_json("other", "llm", "k1") is None                   # entries never cross tenants
    c.put_json("other", "llm", "k1", {"v": 2})
    assert c.purge("acme") == 1 and c.get_json("acme", "llm", "k1") is None and c.get_json("other", "llm", "k1") == {"v": 2}


def test_shared_blob_survives_until_last_entry_goes():
    c = new_cache()
    c.put("t", "llm", "a", b"value")
    c.put("t", "llm", "b", b"value")
    c.discard("t", "llm", "a")
    assert c.get("t", "llm", "b") == b"value"                         # still used by b
    c.discard("t", "llm", "b")
    assert not any(c.blobs.rglob("*")) or all(p.is_dir() for p in c.blobs.rglob("*"))


def test_lru_eviction_by_size():
    c = new_cache(max_bytes=250)
    for k in ("a", "b", "c"):
        c.put("t", "llm", k, k.encode() * 100)                        # 100 bytes each: the third pushes the total over 250
    assert c.get("t", "llm", "a") is None and c.get("t", "llm", "c") is not None
    c.get("t", "llm", "b")                                            # b is now the most recently used
    c.put("t", "llm", "d", b"d" * 100)
    assert c.get("t", "llm", "c") is None and c.get("t", "llm", "b") is not None


def test_key_changes_with_every_input():
    base = key("p", "m", {"x": 1}, "sys", [b"img"])
    assert base == key("p", "m", {"x": 1}, "sys", [b"img"])
    for other in (key("q", "m", {"x": 1}, "sys", [b"img"]), key("p", "m2", {"x": 1}, "sys", [b"img"]), key("p", "m", {"x": 2}, "sys", [b"img"]),
                  key("p", "m", {"x": 1}, "sys2", [b"img"]), key("p", "m", {"x": 1}, "sys", [b"img2"])):
        assert other != base


def test_cached_client_hits_misses_and_discard():
    fake = FakeLLM()
    llm = CachedClient(fake, new_cache(), "acme")
    img = [FilePart(b"drawing", "image/png"), "read it"]
    first = llm.generate_json("SYS", img)
    again = llm.generate_json("SYS", img)
    assert fake.calls == 1 and again.data == first.data and again.usage == {"cached": 1}
    llm.generate_json("SYS", [FilePart(b"drawing", "image/png"), "read it", "correction: Ø52"])      # a human note = a new call
    llm.generate_json("SYS", [FilePart(b"other drawing", "image/png"), "read it"])                   # another picture = a new call
    assert fake.calls == 3
    llm.discard("SYS", img)                                           # the job found that reply invalid
    llm.generate_json("SYS", img)
    assert fake.calls == 4


def test_failures_are_never_cached():
    fake = FakeLLM()
    llm = CachedClient(fake, new_cache(), "acme")
    fake.fail = True
    try:
        llm.generate_json("SYS", ["x"])
        raise AssertionError("expected LLMError")
    except LLMError:
        pass
    fake.fail = False
    llm.generate_json("SYS", ["x"])
    assert fake.calls == 1                                            # the retry really called the model


def test_settings_are_part_of_the_key():
    fake, cache = FakeLLM(), new_cache()
    CachedClient(fake, cache).generate_json("SYS", ["x"])
    fake.cache_settings = {"thinking": "HIGH"}
    CachedClient(fake, cache).generate_json("SYS", ["x"])
    assert fake.calls == 2


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
