"""S2 immutable evidence revisions, retained image blobs and optimistic publication."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid

_ID = re.compile(r"^[0-9a-f]{12}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_LOCKS = [threading.RLock() for _ in range(64)]


def digest(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _write(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent,prefix=".evidence-")
    try:
        with os.fdopen(fd,"wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)


class EvidenceStore:
    def __init__(self, root): self.root=Path(root)

    def _folder(self, source):
        rid=source.get("id","")
        if not isinstance(rid,str) or not _ID.fullmatch(rid): raise ValueError("Invalid evidence result id")
        return self.root/rid

    def publish(self, source, payload, images, *, expected_parent=None):
        folder=self._folder(source)
        with _LOCKS[int(source["id"],16)%len(_LOCKS)]:
            latest=folder/"latest.json"
            current=json.loads(latest.read_text(encoding="utf-8")) if latest.exists() else None
            if (current or {}).get("revision") != expected_parent:
                raise ValueError("Stale evidence parent; refresh before publishing")
            assets={}
            for label, data in images.items():
                sha=hashlib.sha256(data).hexdigest()
                path=self.root/"blobs"/sha
                if not path.exists(): _write(path,data)
                elif hashlib.sha256(path.read_bytes()).hexdigest()!=sha: raise ValueError("Corrupt evidence image")
                assets[label]=sha
            revision=uuid.uuid4().hex[:12]
            envelope={"contract_version":"evidence/v1","result_id":source["id"],"source_result_hash":digest(source),
                      "revision":revision,"parent_revision":expected_parent,"assets":assets,"payload":payload}
            binding={"revision":revision,"sha256":digest(envelope),"source_result_hash":digest(source)}
            _write(folder/f"{revision}.json",json.dumps(envelope,ensure_ascii=False,allow_nan=False).encode())
            _write(latest,json.dumps(binding).encode())
            return binding

    def load(self, source, binding=None):
        folder=self._folder(source)
        binding=binding or json.loads((folder/"latest.json").read_text(encoding="utf-8"))
        revision=binding.get("revision","")
        if not isinstance(revision,str) or not _ID.fullmatch(revision): raise ValueError("Invalid evidence revision")
        envelope=json.loads((folder/f"{revision}.json").read_text(encoding="utf-8"))
        if (envelope.get("contract_version")!="evidence/v1" or envelope.get("result_id")!=source["id"] or
            envelope.get("revision")!=revision or envelope.get("source_result_hash")!=digest(source) or
            binding.get("source_result_hash")!=digest(source) or binding.get("sha256")!=digest(envelope)):
            raise ValueError("Evidence binding/source mismatch")
        for sha in envelope["assets"].values():
            if not isinstance(sha,str) or not _HASH.fullmatch(sha): raise ValueError("Invalid evidence image identity")
            if hashlib.sha256((self.root/"blobs"/sha).read_bytes()).hexdigest()!=sha:
                raise ValueError("Evidence image changed")
        return envelope
