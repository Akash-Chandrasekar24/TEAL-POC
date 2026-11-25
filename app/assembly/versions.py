"""Which built version of each part goes into the assembly: the best one ever built for this drawing, never simply the newest.

A repair can make a part worse (an engine's rebuilt rod lost its big-end bore: 102 cm3 of overlap where the first rod had 0.12), so every
version a part ever had is a candidate, ranked by
1. how many fits it breaks, measured on its own round faces (check_fits), then
2. the overlap it caused in earlier SolidWorks assembly builds of this drawing, the least it ever caused (a version that was never assembled
   is tried once when it is the newest, otherwise it ranks last), then
3. the newest.
Overlap between two parts counts against both, except at a cavity fit, where only the host must give room (a rod inside a solid piston is
the piston's fault, not the rod's).
"""
from __future__ import annotations

import math


def attributed(overlaps: list[tuple[str, str, str, str, float]], fits: list[dict]) -> dict[str, float]:
    """overlaps: (sha a, part a, sha b, part b, mm3) of ONE assembly build -> {sha: mm3 that version is to blame for in that build}."""
    out: dict[str, float] = {}
    for sha_a, part_a, sha_b, part_b, mm3 in overlaps:
        if part_a == part_b:
            blame = [sha_a]                                          # copies of one part: that part's placement / shape
        else:
            cavity = [f for f in fits if f["kind"] == "cavity" and {f["host"], f["guest"]} == {part_a, part_b}]
            blame = [sha_a if cavity[0]["host"] == part_a else sha_b] if cavity else [sha_a, sha_b]
        for s in dict.fromkeys(blame):
            out[s] = out.get(s, 0.0) + mm3
    return out


def scores(builds: list[dict[str, float]], used: list[set[str]]) -> dict[str, float]:
    """Per version, the least overlap it was to blame for in any build that used it (a build with none: 0)."""
    best: dict[str, float] = {}
    for blame, shas in zip(builds, used):
        for s in shas:
            best[s] = min(best.get(s, math.inf), blame.get(s, 0.0))
    return best


def choose(candidates: list[dict], problems: dict[str, int], known: dict[str, float], current: str | None) -> dict:
    """candidates: [{"sha256", "mtime", ...}]. The best by (fits broken, overlap caused, newest); the current (newest built) version, never
    assembled yet, is tried before any assembled one that breaks no more fits."""
    def key(c):
        sha = c["sha256"]
        score = known.get(sha, -1.0 if sha == current else math.inf)
        return (problems.get(sha, 0), score, -c.get("mtime", 0.0))
    return min(candidates, key=key)
