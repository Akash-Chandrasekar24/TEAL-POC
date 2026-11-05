"""The CACHE (architecture C0 + index): a content-addressed blob store on disk and an index of cache entries in SQLite.

    blobs   storage/cache/blobs/ab/abcdef…   immutable files named by the sha256 of their bytes (the same bytes are stored once)
    index   storage/cache/cache.sqlite       cache_entries(tenant, layer, key) -> blob sha, size, versions, created, last_used

Rules (docs/System_Architecture.docx, section 4.7):
- A key is the sha256 of EVERYTHING that affects the value (make it with `key()`), so an entry never expires by time.
- The caller stores only successful, validated results. The cache never skips a human pause: it only saves recomputing a value.
- Every entry belongs to one tenant (client); entries never cross tenants.
- Writes are atomic (temporary file + rename; the index row is replaced), so two workers writing the same key need no lock.
- Eviction: least recently used, by total size per tenant (CACHE_MAX_MB), plus an optional age limit (CACHE_RETENTION_DAYS).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

_SCHEMA = """CREATE TABLE IF NOT EXISTS cache_entries (
    tenant TEXT NOT NULL, layer TEXT NOT NULL, key TEXT NOT NULL, blob_sha TEXT NOT NULL, size INTEGER NOT NULL,
    versions_json TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL, last_used REAL NOT NULL,
    PRIMARY KEY (tenant, layer, key))"""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def key(*parts: Any) -> str:
    """A cache key from any JSON-able parts (bytes are hashed first). Order matters; dict keys are sorted."""
    norm = [{"sha256": sha256(p)} if isinstance(p, (bytes, bytearray)) else p for p in parts]
    return sha256(json.dumps(norm, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))


class Cache:
    def __init__(self, root: Path | str, max_bytes: int | None = None, retention_days: float | None = None):
        self.root = Path(root)
        self.blobs = self.root / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.retention_s = retention_days * 86400 if retention_days else None
        self._lock = threading.Lock()                       # one connection shared by threads (parallel model calls in later phases)
        self._db = sqlite3.connect(self.root / "cache.sqlite", check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(_SCHEMA)

    # ------------------------------------------------------------ blobs (C0)
    def _blob_path(self, sha: str) -> Path:
        return self.blobs / sha[:2] / sha

    def put_blob(self, data: bytes) -> str:
        sha = sha256(data)
        path = self._blob_path(sha)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)                           # atomic: a reader never sees half a file
        return sha

    def get_blob(self, sha: str) -> bytes | None:
        try:
            data = self._blob_path(sha).read_bytes()
        except FileNotFoundError:
            return None
        return data if sha256(data) == sha else None       # a damaged file is a miss, never wrong data

    # ------------------------------------------------------------ entries
    def get(self, tenant: str, layer: str, k: str) -> bytes | None:
        with self._lock:
            row = self._db.execute("SELECT blob_sha FROM cache_entries WHERE tenant=? AND layer=? AND key=?", (tenant, layer, k)).fetchone()
        if not row:
            return None
        data = self.get_blob(row[0])
        with self._lock:
            if data is None:                                # the blob is gone or damaged: drop the entry
                self._db.execute("DELETE FROM cache_entries WHERE tenant=? AND layer=? AND key=?", (tenant, layer, k))
            else:
                self._db.execute("UPDATE cache_entries SET last_used=? WHERE tenant=? AND layer=? AND key=?", (time.time(), tenant, layer, k))
        return data

    def put(self, tenant: str, layer: str, k: str, value: bytes, versions: dict | None = None) -> None:
        sha = self.put_blob(value)
        now = time.time()
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO cache_entries VALUES (?,?,?,?,?,?,?,?)",
                             (tenant, layer, k, sha, len(value), json.dumps(versions or {}, sort_keys=True), now, now))
        self.evict(tenant)

    def get_json(self, tenant: str, layer: str, k: str) -> Any | None:
        data = self.get(tenant, layer, k)
        return None if data is None else json.loads(data)

    def put_json(self, tenant: str, layer: str, k: str, value: Any, versions: dict | None = None) -> None:
        self.put(tenant, layer, k, json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"), versions)

    def discard(self, tenant: str, layer: str, k: str) -> None:
        """Forget one entry (a reply that turned out not to be valid). Its blob goes when nothing else uses it."""
        with self._lock:
            row = self._db.execute("SELECT blob_sha FROM cache_entries WHERE tenant=? AND layer=? AND key=?", (tenant, layer, k)).fetchone()
            self._db.execute("DELETE FROM cache_entries WHERE tenant=? AND layer=? AND key=?", (tenant, layer, k))
        if row:
            self._drop_unused_blobs([row[0]])

    # ------------------------------------------------------------ housekeeping
    def evict(self, tenant: str) -> int:
        """Drop entries older than the retention limit, then the least recently used until the tenant is under max_bytes. -> entries dropped."""
        dropped: list[tuple[str, str]] = []
        with self._lock:
            if self.retention_s:
                old = self._db.execute("SELECT layer, key, blob_sha FROM cache_entries WHERE tenant=? AND created<?",
                                       (tenant, time.time() - self.retention_s)).fetchall()
                dropped += [(r[1], r[2]) for r in old]
                self._db.execute("DELETE FROM cache_entries WHERE tenant=? AND created<?", (tenant, time.time() - self.retention_s))
            if self.max_bytes:
                total = self._db.execute("SELECT COALESCE(SUM(size),0) FROM cache_entries WHERE tenant=?", (tenant,)).fetchone()[0]
                # ponytail: scans the tenant's rows by last_used on every over-limit put; fine for thousands of entries, add an index if it grows
                for layer, k, sha, size in self._db.execute("SELECT layer, key, blob_sha, size FROM cache_entries WHERE tenant=? ORDER BY last_used",
                                                            (tenant,)).fetchall():
                    if total <= self.max_bytes:
                        break
                    self._db.execute("DELETE FROM cache_entries WHERE tenant=? AND layer=? AND key=?", (tenant, layer, k))
                    dropped.append((k, sha))
                    total -= size
        self._drop_unused_blobs([sha for _, sha in dropped])
        return len(dropped)

    def purge(self, tenant: str | None = None, layer: str | None = None) -> int:
        """Delete every entry of a tenant (and/or layer); None = all. -> entries deleted."""
        where, args = [], []
        if tenant is not None:
            where.append("tenant=?"), args.append(tenant)
        if layer is not None:
            where.append("layer=?"), args.append(layer)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with self._lock:
            shas = [r[0] for r in self._db.execute(f"SELECT blob_sha FROM cache_entries{clause}", args).fetchall()]
            self._db.execute(f"DELETE FROM cache_entries{clause}", args)
        self._drop_unused_blobs(shas)
        return len(shas)

    def stats(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT tenant, layer, COUNT(*), COALESCE(SUM(size),0) FROM cache_entries GROUP BY tenant, layer").fetchall()
        return [{"tenant": t, "layer": layer, "entries": n, "bytes": b} for t, layer, n, b in rows]

    def _drop_unused_blobs(self, shas: list[str]) -> None:
        for sha in set(shas):
            with self._lock:
                used = self._db.execute("SELECT 1 FROM cache_entries WHERE blob_sha=? LIMIT 1", (sha,)).fetchone()
            if not used:
                self._blob_path(sha).unlink(missing_ok=True)

    def close(self) -> None:
        self._db.close()


_DEFAULT: Cache | None = None


def default_cache() -> Cache:
    """The project's cache (storage/cache, limits from .env), opened once per process."""
    global _DEFAULT
    if _DEFAULT is None:
        from app import config
        _DEFAULT = Cache(config.CACHE_DIR, max_bytes=int(config.CACHE_MAX_MB * 1024 * 1024) or None,
                         retention_days=config.CACHE_RETENTION_DAYS or None)
    return _DEFAULT
