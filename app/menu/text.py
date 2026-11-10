"""The menu as the LLM sees it: generated from the registry and the parameter models, so it cannot drift from the code.

- menu_overview(): one line per feature (pass 1 of a two-pass menu: pick the features, spend few tokens).
- feature_card(name): the full description of one feature with its JSON schema (pass 2).
- menu_text(): overview + every card, the version the reader prompt carries today.
"""
from __future__ import annotations

import json
from typing import Any

from .registry import MENU


def _inline(schema: dict) -> dict:
    """Pydantic's JSON schema with every $ref replaced by its definition (providers handle plain nested schemas better)."""
    defs = schema.get("$defs", {})

    def walk(node: Any, seen: tuple = ()) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].rsplit("/", 1)[-1]
                if name in seen:
                    return {}
                return walk(defs[name], seen + (name,))
            return {k: walk(v, seen) for k, v in node.items() if k not in ("$defs", "discriminator")}      # the discriminator's mapping holds $ref strings
        if isinstance(node, list):
            return [walk(v, seen) for v in node]
        return node

    return walk(schema)


def params_schema(name: str) -> dict:
    return _inline(MENU[name].params_model.model_json_schema())


def menu_overview() -> str:
    return "\n".join(f"- {e.name}: {e.summary}" for e in MENU.values())


def feature_card(name: str) -> str:
    e = MENU[name]
    limits = "\n".join(f"  - {x}" for x in e.limits)
    return (f"### {e.name}\n{e.description}\nLimits:\n{limits}\nExample `params`: {json.dumps(e.example)}\n"
            f"`params` JSON schema: {json.dumps(params_schema(name), separators=(',', ':'))}")


def _shared_types() -> dict[str, dict]:
    """Named object types (Pos, the outline shapes ...) used by two or more places of the menu: printed once, referred to by name."""
    seen: dict[str, list] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and node.get("title") and "properties" in node:
                seen.setdefault(node["title"], []).append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for name in MENU:
        walk(params_schema(name))
    return {t: nodes[0] for t, nodes in seen.items() if len(nodes) > 1 and all(n == nodes[0] for n in nodes)}


def _by_name(node: Any, shared: dict, top: bool = True) -> Any:
    if isinstance(node, dict):
        if not top and node.get("title") in shared and node == shared[node["title"]]:
            return {"type": node["title"]}
        return {k: _by_name(v, shared, False) for k, v in node.items()}
    if isinstance(node, list):
        return [_by_name(v, shared, False) for v in node]
    return node


def menu_text() -> str:
    """The reader's menu. A type used in several features (a position, an outline shape) is printed ONCE under "Shared types" and named in
    the feature schemas as {"type": "<Name>"}: the same information, about a third of the size (every build list is one call with it)."""
    shared = _shared_types()
    types = "\n".join(f"- {t}: {json.dumps(_by_name(sch, shared), separators=(',', ':'))}" for t, sch in sorted(shared.items()))
    cards = []
    for n in MENU:
        e = MENU[n]
        limits = "\n".join(f"  - {x}" for x in e.limits)
        cards.append(f"### {e.name}\n{e.description}\nLimits:\n{limits}\nExample `params`: {json.dumps(e.example)}\n"
                     f"`params` JSON schema: {json.dumps(_by_name(params_schema(n), shared), separators=(',', ':'))}")
    return ("## Feature menu (the only features you may use)\n" + menu_overview() + "\n\n## Shared types\n"
            "A schema written {\"type\": \"<Name>\"} means the object type <Name> defined here.\n" + types + "\n\n" + "\n\n".join(cards))
