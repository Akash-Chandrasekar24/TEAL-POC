"""The SOLIDWORKS AGENT: back and forth with SolidWorks while the part is built. The runner builds the approved list step by step (no model
call while the steps come out right). When a step goes wrong (SolidWorks refused it, its check failed, or it left a floating piece) the agent
gets what SolidWorks really made and calls TOOLS until the step is right or it gives up; when every step is done it looks at the part beside
the drawing and may add what is missing.

Tools (one JSON call per reply: {"tool": ..., "args": {...}, "why": ...}):
  <feature>   any feature of the menu, args = its params. SolidWorks builds it at once; the reply is what it really made: volume added /
              removed against the expected, solid bodies, the part's size, the position checks, SolidWorks' own error. Every feature checks
              itself and is taken out again when it is wrong, so a call can never leave a broken part behind.
  describe    a feature's full card (limits, example, params schema)
  measure     the part now: size, volume, bodies
  look        pictures of the part now (top, front, side, 3D), beside the drawing
  keep / skip / done   end the turn (keep a built step whose check failed; drop the step; the review is finished)
The model only ever returns JSON (app.llm.base): the tool protocol is plain JSON, so it works with every provider.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from app.llm.base import FilePart, LLMError

COMMON = """Tools: reply with ONE tool call as JSON only: {"tool": "<name>", "args": {...}, "why": "<one short line>"}.
- <feature name> (any feature of the menu below): args = that feature's `params`. SolidWorks builds it AT ONCE and you get back what it really
  made: the volume added / removed against what the feature expected, the number of solid bodies, the part's size, the position checks and
  SolidWorks' own error text. A wrong result is taken out again automatically.
- describe {"feature": "<name>"}: that feature's full card (limits, example, params JSON schema). Read it before you use a feature whose
  params you have not seen in this conversation.
- measure {}: the part now: bounding box (mm), volume, number of solid bodies.
- look {}: a picture of the part now (TOP, FRONT/elevation, SIDE and 3D), to compare with the drawing.
Positions and sizes come from the drawing and the list (same frames and units as the list). Never invent a number the drawing does not give.
Read each result before your next call; never send a call that already failed the same way again."""

FIX = """You are the CAD engineer at the SolidWorks screen. A build list is being built step by step. The step below went wrong in SolidWorks.
Find out why from what SolidWorks reported, and make that step right.
""" + COMMON + """
- keep {}: only when the step WAS built and its check failed but the shape is what the drawing shows (e.g. a hole crossing a pocket removes less).
- skip {"reason": "..."}: give up on this step (it is taken out); the designer is told why.
A feature call REPLACES the step that went wrong (what it built before is taken out first). The fix is finished when a feature call comes back ok."""

REVIEW = """You are the CAD engineer at the SolidWorks screen. Every step of the build list has been built. Compare the part with the
drawing (use look and measure) and add what is missing or clearly wrong: a hole, slot, cut, boss or rounding the drawing shows and the part does not.
""" + COMMON + """
- done {"summary": "..."}: the part matches the drawing as far as the menu can make it.
A feature call ADDS a new step at the end. `items_in_the_part` lists every list item that is in the part (the base and its side shape E1 are
built as ONE step): never build those again. A step that is listed as not built may be retried with corrected params. The cards of the
likely features are in `feature_cards`: no describe call is needed for them. Spend your calls on what is missing."""


@dataclass
class AgentLog:
    turns: int = 0
    calls: list[dict] = field(default_factory=list)       # {"phase", "item", "tool", "args", "why", "result"}

    def add(self, phase: str, item: str, act: dict, result: dict | None) -> None:
        self.calls.append({"phase": phase, "item": item, "tool": act.get("tool"), "args": act.get("args"), "why": str(act.get("why", ""))[:300],
                           "result": _short(result)})


def _short(result: dict | None) -> dict | None:
    if result is None:
        return None
    return {k: v for k, v in result.items() if k not in ("image",)}


def side_by_side(top: bytes, bottom: bytes, width: int = 1600) -> bytes:
    """The drawing above the part as built, in one PNG (a provider may take one picture per call)."""
    import io
    from PIL import Image
    a, b = (Image.open(io.BytesIO(x)).convert("RGB") for x in (top, bottom))
    a, b = (im.resize((width, max(1, round(im.height * width / im.width)))) for im in (a, b))
    out = Image.new("RGB", (width, a.height + b.height + 12), "white")
    out.paste(a, (0, 0)); out.paste(b, (0, a.height + 12))
    buf = io.BytesIO(); out.save(buf, "PNG")
    return buf.getvalue()


Run = Callable[[str, dict], Awaitable[dict]]            # (tool, args) -> result; the runner executes it in SolidWorks


class SolidWorksAgent:
    """`llm`: a JSON model client (generate_json). `picture`: the drawing (PNG bytes). Turn limits keep every loop finite."""

    def __init__(self, llm, picture: bytes | None, menu_overview: str, *, fix_turns: int = 6, review_turns: int = 8, total_turns: int = 1000):
        self.llm, self.picture, self.menu = llm, picture, menu_overview
        self.fix_turns, self.review_turns = fix_turns, review_turns
        self.total_turns = total_turns          # one budget for the whole part (stress test: 71 model calls, 22 minutes on one part with many failing steps)
        self.log = AgentLog()

    async def _ask(self, system: str, context: dict, image: bytes | None) -> dict:
        parts: list = []
        shown = side_by_side(self.picture, image) if (self.picture and image) else (self.picture or image)
        if shown:                                                               # ONE picture per call (Domo takes one): drawing above, the part below
            parts.append(FilePart(shown, "image/png"))
        parts.append(("The picture shows the DRAWING on top and the part AS BUILT NOW below it." if self.picture and image else
                      "The picture is the drawing." if self.picture else "The picture is the part as built now." if image else "")
                     + "\n" + json.dumps(context, ensure_ascii=False, default=str))
        self.log.turns += 1
        reply = await asyncio.to_thread(self.llm.generate_json, system + "\n\n## Feature menu\n" + self.menu, parts)
        return reply.data if isinstance(reply.data, dict) else {}

    async def _loop(self, phase: str, item: str, system: str, context: dict, run: Run, turns: int, stop: set[str],
                    finished: Callable[[dict, dict], bool], image: bytes | None = None) -> dict:
        history: list[dict] = []
        for _ in range(turns):
            if self.log.turns >= self.total_turns:                              # the part's budget is used up: the rest is flagged for the designer
                return {"tool": "skip", "args": {"reason": f"the SolidWorks agent's budget of {self.total_turns} model calls for this part is used up"}}
            try:
                act = await self._ask(system, {**context, "your_calls_so_far": history[-10:]}, image)
            except LLMError as ex:                                              # the model is not reachable: the build goes on without it
                return {"tool": "skip", "args": {"reason": f"the model call failed: {ex}"[:300]}}
            image = None
            tool = str(act.get("tool") or "")
            if tool in stop:
                self.log.add(phase, item, act, None)
                return act
            args = act.get("args") if isinstance(act.get("args"), dict) else {}
            result = await run(tool, args) if tool else {"status": "refused", "message": "no tool named: reply {\"tool\": ..., \"args\": {...}}"}
            self.log.add(phase, item, act, result)
            if result.get("image"):
                image = Path(result["image"]).read_bytes()
            history.append({"tool": tool, "args": args, "result": _short(result)})
            if finished(act, result):
                return {"tool": tool, "args": args, "result": result}
        return {"tool": "skip", "args": {"reason": f"no answer within {turns} tool calls"}}

    async def fix(self, problem: dict, run: Run) -> dict:
        """One step went wrong: tool calls until a feature call comes back ok, or keep / skip. Returns the last call."""
        return await self._loop("fix", problem.get("item", {}).get("id", "?"), FIX, {"step_that_went_wrong": problem}, run, self.fix_turns,
                                {"keep", "skip"}, lambda act, res: res.get("status") == "ok" and res.get("feature_call", False))

    async def review(self, state: dict, run: Run) -> dict:
        """Every step is done: look, compare, add what is missing; ends with done or when the turns run out."""
        if self.review_turns <= 0:
            return {"tool": "skip", "args": {"reason": "no review turns"}}
        first = await run("look", {})                                           # the picture comes with the first turn: no call spent on asking for it
        image = Path(first["image"]).read_bytes() if first.get("image") else None
        return await self._loop("review", "-", REVIEW, {"build": state}, run, self.review_turns, {"done"}, lambda act, res: False, image)


def agent_for(job_dir: Path) -> SolidWorksAgent | None:
    """The agent for a build, when the job has the drawing (drawing.png, written by the server) and SW_AGENT is not 'off'."""
    picture = Path(job_dir) / "drawing.png"
    if os.getenv("SW_AGENT", "on").strip().lower() in ("off", "0", "false", "no") or not picture.is_file():
        return None
    from app import config
    from app.llm import make_client
    from app.menu.text import menu_overview
    return SolidWorksAgent(make_client(config.WORKER_LLM_PROVIDER, reasoning_effort="medium"), picture.read_bytes(), menu_overview(),
                           fix_turns=int(os.getenv("SW_AGENT_FIX_TURNS", "5")), review_turns=int(os.getenv("SW_AGENT_REVIEW_TURNS", "6")),
                           total_turns=int(os.getenv("SW_AGENT_TOTAL_TURNS", "16")))
