import copy

import fakes  # noqa: F401
from drawing_fixtures import drawing_result, model

from app.drawing.model import Resolutions, reconcile


def test_readings_keep_both_sources_and_distinct_equal_dimensions():
    m = model()
    assert m.version == "1" and m.issues == []
    assert len(m.dimensions) == 8
    equal = [d for d in m.dimensions if d.text == "15"]
    assert len(equal) == 2 and equal[0].id != equal[1].id
    assert {r.source for r in m.dimensions[0].readings} == {"page", "crop"}
    assert m.pages[0].source == "plate.png" and m.dimensions[2].views == ["P1.v2"]


def test_conflicting_metadata_requires_explicit_resolution_without_changing_sources():
    result = drawing_result()
    second = copy.deepcopy(result["pages"][0])
    second.update(no=2, source_page=2)
    second["read"].update(dims=[], views=[], title_block={"unit": "inch", "projection": "first"})
    result["pages"].append(second)
    m = reconcile(result)
    assert m.unit == m.projection == "unknown"
    assert any("conflicting" in reason for reason in m.issues)
    fixed = reconcile(result, Resolutions(unit="mm", projection="third"))
    assert fixed.issues == [] and fixed.unit == "mm" and fixed.projection == "third"
    assert fixed.pages[1].title_block["unit"] == "inch"
    assert fixed.source_hash == m.source_hash and fixed.digest() != m.digest()


def test_a_human_correction_is_auditable_and_does_not_modify_raw_result():
    result = drawing_result()
    original = copy.deepcopy(result)
    result["pages"][0]["read"]["dims"][0]["single_source"] = True
    assert any("P1.d1" in q for q in reconcile(result).issues)
    resolutions = Resolutions.model_validate({"dimensions": [{"id": "P1.d1", "text": "125"}]})
    m = reconcile(result, resolutions)
    assert not m.issues
    assert m.dimensions[0].text == original["pages"][0]["read"]["dims"][0]["text"] == "120"
    assert m.dimensions[0].nominal() == 125 and m.dimensions[0].confirmed
    assert result["pages"][0]["read"]["dims"][0]["text"] == "120"


def test_unknown_duplicate_and_unconfirmed_corrections_are_rejected():
    for decisions in ([{"id": "P9.d1"}], [{"id": "P1.d1"}, {"id": "P1.d1"}],
                      [{"id": "P1.d1", "text": "9", "confirmed": False}]):
        try:
            reconcile(drawing_result(), Resolutions.model_validate({"dimensions": decisions}))
        except ValueError:
            continue
        raise AssertionError(decisions)


def test_ambiguous_legacy_row_needs_a_specific_corrected_value():
    result = drawing_result()
    dim = result["pages"][0]["read"]["dims"][0]
    dim.update(text="120 x 80", token="")
    m = reconcile(result, Resolutions.model_validate({"dimensions": [{"id": "P1.d1"}]}))
    assert m.issues
    m = reconcile(result, Resolutions.model_validate({"dimensions": [{"id": "P1.d1", "text": "120"}]}))
    assert not m.issues and m.dimensions[0].nominal() == 120


def test_unread_pages_and_fractional_callouts_do_not_become_silent_lengths():
    result = drawing_result()
    result["pages"][0]["read"] = None
    assert any("read dimensions" in q for q in reconcile(result).issues)
    m = model()
    d = m.dimensions[0]
    d.text, d.token = "1/2", "1"
    try:
        d.nominal()
    except ValueError as ex:
        assert "fractional" in str(ex)
    else:
        raise AssertionError("fraction parsed as an integer")


def test_negative_correction_retains_its_sign():
    m = reconcile(drawing_result(), Resolutions.model_validate({"dimensions": [{"id": "P1.d5", "text": "-5"}]}))
    assert not m.issues and m.dimensions[4].nominal() == -5
