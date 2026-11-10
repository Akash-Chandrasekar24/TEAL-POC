"""Water pump sheets (Water_Pump_Motor_Engineering_Drawings_3_Pages.pdf): the six build lists the existing features can make.

Each list must (1) wait for the designer where the sheet is unclear or a gap is not modelled, (2) once answered, compile every feature and pass the
upstream IR validator, and (3) give the volume a hand calculation gives. Holes / chamfers on flat parts are placed against real faces inside
SolidWorks, so for those only the body volume is checked here.
"""
import json
from math import isclose, pi
from pathlib import Path

import fakes  # noqa: F401
from app.features.base import FeatureRequestError
from app.mcp_client.backend import validate_graph
from app.menu import parse_reply
from app.runner import prepare, validate_list

ROOT = Path(__file__).resolve().parents[1]

# closed forms from the printed dimensions (see each list's derived_from)
VOLUMES = {
    "pump_shaft": pi * (144 * 28 + 225 * 42 + 306.25 * 30 + 625 * 38 + 306.25 * 42 + 225 * 30 + 156.25 * 40 + 100 * 30)
                  - (pi * 144 * 2 - pi * 2 / 3 * (100 + 120 + 144)) - (pi * 100 * 2 - pi * 2 / 3 * (64 + 80 + 100)),   # minus the two end chamfers
    "pump_coupling_half": pi * (35 ** 2 - 15 ** 2) * 12 - pi * 2 * (2 * 35 - 4 / 3) + pi * (23 ** 2 - 15 ** 2) * 28 - 4 * pi * 5.25 ** 2 * 12,
    "pump_seal_seat": pi * ((30 ** 2 - 17.5 ** 2) * 12 + (25 ** 2 - 12.5 ** 2) * 18 + (25 ** 2 - 21 ** 2) * 6),
    "pump_suction_flange": pi / 4 * (110 ** 2 - 50 ** 2) * 18,                              # body; the 4 holes and the chamfer are placed in SolidWorks
    "pump_discharge_flange": pi / 4 * (110 ** 2 - 40 ** 2) * 16,
    "pump_shaft_key": 40 * 10 * 4.5,
}
WAITS_FOR = {"pump_shaft": 2, "pump_coupling_half": 1, "pump_seal_seat": 1, "pump_suction_flange": 1, "pump_discharge_flange": 1, "pump_shaft_key": 1}


def test_every_pump_list_waits_for_the_designer_then_compiles_with_the_hand_volume():
    for name, volume in VOLUMES.items():
        data = json.loads((ROOT / "eval/build_lists" / f"{name}.json").read_text(encoding="utf-8"))
        assert len(validate_list(parse_reply(data))) == WAITS_FOR[name], name             # questions / uncertain readings / gaps hold the build
        data["accepted_gaps"] = [u["id"] for u in data["unsupported"]]
        data["needs_human"] = []
        for item in data["items"]:
            item["uncertain"] = False                                                      # the designer confirmed the reading
        bl = parse_reply(data)
        assert validate_list(bl) == [], name
        steps = [x for p in prepare(bl) for x in (p.feature.steps() if hasattr(p.feature, "steps") else [p.feature])]
        total = 0.0
        for step in steps:
            if not hasattr(step, "graph"):
                continue
            try:
                graph = step.graph()
            except FeatureRequestError:                                                     # face-placed: resolved inside SolidWorks
                continue
            assert validate_graph(graph) == [], name
            total += step.expected_delta_mm3()
        tol = 250 if name == "pump_shaft" else 1                                           # the shaft's two R2 fillets add ~200 mm3
        assert abs(total - volume) <= tol, (name, total, volume)
