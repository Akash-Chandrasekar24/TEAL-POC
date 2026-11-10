"""The build lists for the pump and Wankel parts that had none (written by scripts/write_missing_part_lists.py).

Each must (1) hold the build until the designer confirms the unprinted (ASSUMED) numbers and accepts the gaps, and (2) once confirmed, compile
every feature and pass upstream's IR validator. Simple prisms / pins are also checked against their hand volume."""
import json
from math import pi
from pathlib import Path

import fakes  # noqa: F401
from app.features.base import FeatureRequestError
from app.mcp_client.backend import validate_graph
from app.menu import parse_reply
from app.runner import prepare, validate_list

ROOT = Path(__file__).resolve().parents[1]
WAITS = {"pump_casing": 3, "pump_motor_housing": 3, "pump_impeller": 2, "pump_cooling_fan": 3, "pump_bearing_housing": 3,
         "wankel_rotor_housing": 3, "wankel_side_housing": 3, "wankel_rotor": 3, "wankel_apex_seal": 1, "wankel_side_seal": 0,
         "wankel_corner_seal": 1, "wankel_dowel": 2}
VOLUMES = {"wankel_apex_seal": 38 * 3 * 3, "wankel_side_seal": 32 * 2.5 * 2.5, "wankel_corner_seal": 12 * 2 * 3, "wankel_dowel": pi * 16 * 24,
           "pump_cooling_fan": pi * (30 ** 2 - 14 ** 2) * 32 + 12 * 3 * 50 * 32}


def test_missing_part_lists_wait_for_the_designer_then_compile():
    for name, waits in WAITS.items():
        data = json.loads((ROOT / "eval/build_lists" / f"{name}.json").read_text(encoding="utf-8"))
        assert len(validate_list(parse_reply(data))) == waits, name
        data["accepted_gaps"] = [u["id"] for u in data["unsupported"]]
        data["needs_human"] = []
        for it in data["items"]:
            it["uncertain"] = False
        bl = parse_reply(data)
        assert validate_list(bl) == [], (name, validate_list(bl))
        total = 0.0
        for p in prepare(bl):
            for step in (p.feature.steps() if hasattr(p.feature, "steps") else [p.feature]):
                if hasattr(step, "graph"):
                    try:
                        graph = step.graph()
                    except FeatureRequestError:                     # placed on a real face inside SolidWorks
                        continue
                    assert validate_graph(graph) == [], name
                elif hasattr(step, "spec"):
                    step.spec()
                if hasattr(step, "expected_delta_mm3"):
                    total += step.expected_delta_mm3()
        if name in VOLUMES:
            tol = 1e-4 if name == "pump_cooling_fan" else 1e-6                # the fan: blade feet follow the round hub (not flat-ended)
            assert abs(total - VOLUMES[name]) <= tol * VOLUMES[name] + 1e-6, (name, total, VOLUMES[name])
