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


def menu_text() -> str:
    return "## Feature menu (the only features you may use)\n" + menu_overview() + "\n\n" + "\n\n".join(feature_card(n) for n in MENU)
