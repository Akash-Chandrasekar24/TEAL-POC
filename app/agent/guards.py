"""Guards: the approved list is fixed, and an LLM suggestion may change only what a human then approves.

- list_hash: a stable hash of the approved list. The build refuses to run a list whose hash differs from the approved one.
- diff_params: the exact changes of a proposal, in words a human can check (path, old value, new value).
- check_repair: a repair suggestion is acceptable only if it names the failed item, changes only that item's `params`, still fits the menu,
  and actually changes something. It is never applied before a human approves it.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from pydantic import ValidationError

from app.menu import MENU


def list_hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _flatten(value: Any, path: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            out.update(_flatten(v, f"{path}.{k}" if path else k))
        return out
    if isinstance(value, list):
        out = {}
        for i, v in enumerate(value):
            out.update(_flatten(v, f"{path}[{i}]"))
        return out
    return {path: value}


def diff_params(old: dict, new: dict) -> list[dict]:
    """[{"path", "old", "new"}] for every leaf that differs (a missing leaf shows as None)."""
    a, b = _flatten(old), _flatten(new)
    return [{"path": p, "old": a.get(p), "new": b.get(p)} for p in sorted(set(a) | set(b)) if a.get(p) != b.get(p)]


def find_item(data: dict, item_id: str) -> dict | None:
    return next((it for it in data.get("items", []) if it.get("id") == item_id), None)


HUMAN_ONLY_FLAGS = ("floor_open", "edge_zone_ok")           # switch a check off: only a human may change them (expected_final / accepted_gaps live outside `params`)


def check_repair(approved: dict, failed_item_id: str, reply: Any, human: bool = False) -> tuple[bool, str, dict | None]:
    """(ok, reason, the proposed complete params). ok=False: not usable, with the reason; a needs_human answer returns (False, reason, None).
    `human=True` only for params a human wrote themself (edit_item): then the human-only flags may change."""
    if not isinstance(reply, dict):
        return False, "the repair reply is not a JSON object", None
    if reply.get("status") == "needs_human":
        return False, f"the repair step asks for a human: {reply.get('reason', '')}", None
    if reply.get("status") != "suggestion":
        return False, f"unknown repair status {reply.get('status')!r}", None
    if reply.get("item_id") != failed_item_id:
        return False, f"the suggestion is for {reply.get('item_id')!r}, the failed item is {failed_item_id!r}: only the failed item may change", None
    extra = set(reply) - {"status", "item_id", "params", "reason"}
    if extra:
        return False, f"the suggestion has fields that are not allowed: {sorted(extra)} (only params may change)", None
    item = find_item(approved, failed_item_id)
    if item is None:
        return False, f"item {failed_item_id!r} is not in the approved list", None
    params = reply.get("params")
    if not isinstance(params, dict):
        return False, "the suggestion has no params object", None
    entry = MENU.get(item.get("feature"))
    if entry is None:
        return False, f"feature {item.get('feature')!r} is not on the menu", None
    try:
        entry.params_model.model_validate(params)
    except ValidationError as ex:
        first = ex.errors()[0]
        return False, f"the suggested params do not fit {item['feature']}: {'.'.join(str(p) for p in first['loc'])}: {first['msg']}", None
    if not human:
        flipped = [k for k in HUMAN_ONLY_FLAGS if bool(params.get(k, False)) != bool(item.get("params", {}).get(k, False))]
        if flipped:
            return False, f"the suggestion changes {flipped}: only a human may change those (they switch a check off). Fix the item some other way, or answer needs_human", None
    if not diff_params(item.get("params", {}), params):
        return False, "the suggestion changes nothing", None
    return True, str(reply.get("reason", "")), params


def apply_repair(approved: dict, failed_item_id: str, params: dict) -> dict:
    """A copy of the approved list with that item's params replaced (the caller has a human's approval)."""
    new = copy.deepcopy(approved)
    find_item(new, failed_item_id)["params"] = copy.deepcopy(params)
    return new
