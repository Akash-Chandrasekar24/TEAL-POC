"""Worker boundaries around the existing extraction and CAD implementations.

Workers keep the existing prompts, result schemas, cache and verification code.
The extraction worker has no CAD execution methods. Model clients are lazy so
offline validation and approved assembly execution need no provider credentials.
"""
from __future__ import annotations

from pathlib import Path

from app.llm.base import LLMClient
from .harness import HarnessClient


class _ModelWorker:
    effort_setting = "CAD_REASONING_EFFORT"

    def __init__(self, llm: LLMClient | None = None, *, provider: str | None = None, cache: bool | None = None):
        from app import config
        self._llm = llm
        self.provider, self.cache = provider or config.WORKER_LLM_PROVIDER, cache

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            from app import config
            from app.llm import make_client
            self._llm = make_client(self.provider, cache=self.cache, reasoning_effort=getattr(config, self.effort_setting))
        return self._llm

    def identity(self) -> dict:
        """Monitoring metadata without constructing a client or reading secrets."""
        from app import config
        provider = getattr(self._llm, "provider", None) or self.provider or config.LLM_PROVIDER
        model = getattr(self._llm, "model", None)
        if model is None:
            model = config.DOMO_MODEL if provider == "domo" else config.GEMINI_MODEL
        return {"provider": provider, "model": model}

    @property
    def model_client(self):
        return HarnessClient(self.llm)

    def discard(self, system, parts) -> None:
        getattr(self.llm, "discard", lambda *a: None)(system, parts)


class ExtractionAgent(_ModelWorker):
    """Extract dimensions, view/feature observations and BOM evidence only."""

    effort_setting = "EXTRACTION_REASONING_EFFORT"

    def assess_page(self, png):
        from app.drawing.readiness import assess_page
        return assess_page(self.model_client, png)

    def suggest_clarifications(self, page_no, png, questions, views, dimensions):
        """Draft answers for the reviewer to accept or reject. Proposals only: nothing is saved here."""
        from app.drawing.clarify import suggest
        return suggest(self.model_client, page_no, png, questions, views, dimensions)

    def read_page(self, page_no, png, boxes):
        from app.perception.extract import read_page
        return read_page(self.model_client, page_no, png, boxes)

    def read_hybrid(self, page_no, png, ocr, strategy, transport="verbose"):
        from app.llm.domo import effort_override
        from app.perception.hybrid import extract, page_effort
        effort = page_effort(ocr)                       # None = keep the configured effort; only Domo calls read the override
        if effort:
            from app import trace
            trace.current().event("read", "simple page: reduced reasoning effort", page=page_no, effort=effort, ocr_lines=len(ocr.get("lines") or []))
        with effort_override(effort):
            return extract(self.model_client, page_no, png, ocr, strategy, transport=transport)

    def interpret(self, model, pictures):
        from app.drawing.planner import interpret
        return interpret(self.model_client, model, pictures)

    def read_bom(self, result, pictures):
        from app.assembly.bom import read_bom
        return read_bom(self.model_client, result, pictures)

    def read_list(self, parts):
        """Compatibility path: preserve the existing one-call feature-list read.

        This is an unapproved extraction draft, not an execution instruction.
        The graph still parses, validates and requests approval of the draft.
        """
        from .prompts import reader_system
        return self.model_client.generate_json(reader_system(), parts)


class SolidWorksAgent(_ModelWorker):
    """Plan from extracted evidence; delegate execution to the existing code."""

    def __init__(self, llm: LLMClient | None = None, *, build=None, provider=None, cache=None):
        super().__init__(llm, provider=provider, cache=cache)
        self.build = build

    def plan(self, model, interpretations):
        from app.drawing.planner import plan_interpreted
        return plan_interpreted(self.model_client, model, interpretations)

    def repair(self, parts):
        from .prompts import repair_system
        return self.model_client.generate_json(repair_system(), parts)

    def build_part(self, approved, job_dir):
        build = self.build
        if build is None:
            from .live import solidworks_build
            build = solidworks_build
        return build(approved, Path(job_dir))

    async def build_assembly(self, sw, plan, assets, output, approved_hash, *, review_only=False):
        from app.assembly.runner import run_assembly
        return await run_assembly(sw, plan, assets, output, approved_hash, review_only=review_only)

    async def build_sheet(self, sw, plan, output, approved_hash):
        from app.drawing.sheets import run_sheet
        return await run_sheet(sw, plan, output, approved_hash)
