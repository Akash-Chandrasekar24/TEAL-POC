import copy

import fakes  # noqa: F401
from fakes import FakeModel
from drawing_fixtures import binding, interpretations, model, png, proposal

from app.drawing.planner import Binding, PageInterpretation, Proposal, compile_plan, evaluate, plan
from app.menu import parse_reply
from app.runner import validate_list


def test_plate_hole_boss_compiles_to_existing_menu_with_parameter_provenance():
    report = compile_plan(model(), interpretations(), proposal())
    assert report["status"] == "ready_for_approval", report
    assert report["approved"] is False
    assert [i["feature"] for i in report["draft"]["items"]] == ["extrude_profile_boss", "hole", "circle_boss"]
    assert validate_list(parse_reply(report["draft"])) == []
    assert report["draft"]["items"][1]["params"]["material_length"] == 10
    source = report["provenance"]["F2"]["parameters"]["material_length"]["sources"]["d"]
    assert source["id"] == "P1.d3" and source["views"] == ["P1.v2"]


def test_arithmetic_is_checked_and_keeps_sources_and_units():
    value, evidence = evaluate(Binding(expression="(w - h) / 2", sources={"w": "P1.d1", "h": "P1.d2"}, reason="half the printed difference"), model())
    assert value == 20 and evidence["unit"] == "mm" and evidence["derived"]
    assert set(evidence["sources"]) == {"w", "h"}


def test_invented_unsafe_dimensionally_invalid_and_unexplained_expressions_are_refused():
    bad = [Binding(expression="100", reason="invented"), Binding(expression="0"),
           Binding(expression="d / 0", sources={"d": "P1.d1"}, reason="bad division"),
           Binding(expression="d * d", sources={"d": "P1.d1"}, reason="area is not a length"),
           Binding(expression="d + 2", sources={"d": "P1.d1"}, reason="scalar is not a length"),
           Binding(expression="d / 2", sources={"d": "P1.d1"}),
           Binding(expression="d", sources={"d": "P99.d1"}),
           Binding(expression="d", sources={"d": "P1.d1", "extra": "P1.d2"}),
           Binding(expression="__import__('os').system('anything')"),
           Binding(expression="d ** 2", sources={"d": "P1.d1"}, reason="powers forbidden")]
    for b in bad:
        try:
            evaluate(b, model())
        except ValueError:
            continue
        raise AssertionError(b)


def test_unsupported_geometry_is_kept_and_prevents_downloadable_plan():
    pages = interpretations()
    pages[0].features.append(pages[0].features[0].model_copy(update={"id": "P1.f4", "kind": "unsupported", "description": "thread"}))
    p = proposal()
    # Dropping the thread is caught by observation coverage.
    report = compile_plan(model(), pages, p)
    assert any("Unaccounted" in q for q in report["questions"])
    p.features.append(p.features[0].model_copy(update={"id": "F4", "kind": "unsupported", "description": "thread", "observations": ["P1.f4"], "parameters": {}}))
    report = compile_plan(model(), pages, p)
    assert report["status"] == "needs_information" and report["draft"]["unsupported"]
    assert report["draft"]["needs_human"]


def test_wrong_observation_kinds_sources_and_support_dependencies_are_blocked():
    variants = []
    p = proposal()
    p.features[1].parameters["diameter"] = Binding(**binding("P1.d7"))
    variants.append(p)
    p = proposal()
    p.features[1].support = "F3"  # following, not preceding
    variants.append(p)
    p = proposal()
    p.features[1].observations = ["P1.f3"]
    variants.append(p)
    p = proposal()
    p.features[0].parameters.pop("thickness")
    variants.append(p)
    for p in variants:
        report = compile_plan(model(), interpretations(), p)
        assert report["status"] == "needs_information" and report["questions"]


def test_angles_cannot_be_used_as_lengths():
    m = model()
    m.dimensions[0].token = "120°"
    p = proposal()
    assert compile_plan(m, interpretations(), p)["status"] == "needs_information"


def test_distinct_hole_observations_on_one_page_cannot_be_collapsed():
    pages = interpretations()
    pages[0].features.append(pages[0].features[1].model_copy(update={"id": "P1.f4", "description": "second distinct hole"}))
    p = proposal()
    p.features[1].observations.append("P1.f4")
    report = compile_plan(model(), pages, p)
    assert any("cannot be collapsed" in q for q in report["questions"])
    assert report["status"] == "needs_information"


def test_unresolved_readings_stop_before_any_model_call():
    m = model()
    m.issues = ["Confirm the missing unit"]
    llm = FakeModel()
    report = plan(llm, m, {})
    assert report["status"] == "needs_review" and report["calls"] == 0 and llm.calls == []


def test_visual_interpretation_then_text_planning_use_one_picture_per_page():
    llm = FakeModel(reads=[interpretations()[0].model_dump(), proposal().model_dump()])
    report = plan(llm, model(), {1: png()})
    assert report["status"] == "ready_for_approval" and report["calls"] == 2
    assert len(llm.calls) == 2
    assert sum(not isinstance(p, str) for p in llm.calls[0][1]) == 1
    assert all(isinstance(p, str) for p in llm.calls[1][1])


def test_unknown_visual_references_and_bad_json_schemas_are_refused():
    class DiscardingModel(FakeModel):
        def discard(self, system, parts):
            self.discarded = True

    page = interpretations()[0].model_dump()
    page["features"][0]["dimensions"]["width"] = ["P99.d1"]
    for reply in (page, {"features": "not a list"}):
        llm = DiscardingModel(reads=[reply])
        try:
            plan(llm, model(), {1: png()})
        except ValueError:
            assert llm.discarded
            continue
        raise AssertionError(reply)


def test_an_observed_feature_can_combine_location_and_depth_across_pages():
    m = model()
    page2 = m.pages[0].model_copy(update={"no": 2, "source_page": 2, "views": []})
    m.pages.append(page2)
    depth = m.dimensions[2].model_copy(update={"id": "P2.d1", "page": 2, "views": []})
    m.dimensions.append(depth)
    pages = interpretations()
    extra = pages[0].features[1].model_copy(update={"id": "P2.f1", "views": [], "dimensions": {"material_length": ["P2.d1"]}})
    pages.append(PageInterpretation(features=[extra]))
    p = proposal()
    p.features[1].observations.append("P2.f1")
    p.features[1].parameters["material_length"] = Binding(**binding("P2.d1"))
    report = compile_plan(m, pages, p)
    assert report["status"] == "ready_for_approval"
    assert report["provenance"]["F2"]["parameters"]["material_length"]["sources"]["d"]["page"] == 2
