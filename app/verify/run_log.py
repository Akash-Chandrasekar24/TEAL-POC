"""Per-run log: logs/runs/<run_id>/run.jsonl (every step, bulky) and summary.md (one line per step)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app import config

RUNS_DIR = config.LOG_DIR / "runs"


class RunLog:
    def __init__(self, name: str, root: Path | None = None):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.dir = (root or RUNS_DIR) / f"{stamp}_{name}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.name = name
        self._jsonl = self.dir / "run.jsonl"
        self._steps: list[dict[str, Any]] = []

    def event(self, kind: str, **data: Any) -> None:
        entry = {"ts": datetime.now().isoformat(timespec="milliseconds"), "kind": kind, **data}
        with open(self._jsonl, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def step(self, record: dict[str, Any]) -> None:
        self._steps.append(record)
        self.event("step", **record)

    def write_summary(self, title: str = "", notes: list[str] | None = None) -> Path:
        """Short markdown: one line per step."""
        lines = [f"# {title or self.name}", f"Run folder: {self.dir.name}", ""]
        for i, s in enumerate(self._steps, 1):
            exp = s.get("expected_delta_mm3")
            lines.append(f"- S{i:02d} {s['name']}: {'OK' if s['ok'] else 'FAILED'} ({s['verdict']}) | "
                         f"expected {('%.1f' % exp) if exp is not None else 'n/a'} mm3, measured {s['measured_delta_mm3']:.1f} mm3"
                         + (f" | bound {s['bound_mm3']:.1f}" if s.get("bound_mm3") is not None else "")
                         + (f" | regression: {s['regression']}" if s.get("regression") else "")
                         + f" | {s.get('message', '')}")
        if notes:
            lines += ["", "## Notes", *[f"- {n}" for n in notes]]
        path = self.dir / "summary.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path
