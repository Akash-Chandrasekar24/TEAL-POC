"""AI-drafted clarification answers are proposals: grounded, validated, never saved by this step."""
import json
from pathlib import Path
from unittest.mock import patch

import fakes  # noqa: F401
from drawing_fixtures import drawing_result, model

from app.agent.orchestrator import Orchestrator
from app.agent.workers import ExtractionAgent
from app.drawing.clarify import open_questions, suggest
from app.drawing.model import Resolutions, reconcile
from app.llm.base import LLMResponse


class Scripted:
    model = "fake"
    provider = "fake"

    def __init__(self, data):
        self.data, self.calls = data, []

    def generate_json(self, system, parts):
        self.calls.append((system, parts))
        return LLMResponse(json.dumps(self.data), self.data, "fake", 0, {})


def with_questions():
    result = drawing_result()
    result["pages"][0]["read"]["issues"] = ["The small dimension reads 42 but 22 is plausible", "Is the projection symbol first angle?", "Scale label"]
    return result


VIEWS = [{"id": "P1.v1", "label": "PLAN", "type": "plan"}, {"id": "P1.v2", "label": "SECTION A-A", "type": "section"}]


def test_open_questions_skip_the_ones_already_answered():
    result = with_questions()
    m = reconcile(result)
    assert [q["key"] for q in open_questions(result, m)] == ["P1.u1", "P1.u2", "P1.u3"]
    answered = reconcile(result, Resolutions.model_validate({"clarifications": [{"id": "P1.u2", "answer": "It is first angle", "text": None, "views": []}]}))
    assert [q["key"] for q in open_questions(result, answered)] == ["P1.u1", "P1.u3"]


def test_proposals_are_cleaned_and_every_asked_question_is_accounted_for():
    questions = [{"key": "P1.u1", "question": "q1"}, {"key": "P1.u2", "question": "q2"}, {"key": "P1.u3", "question": "q3"}]
    llm = Scripted({"suggestions": [
        {"key": "P1.u1", "can_answer": True, "answer": "It prints 22.", "measurement": "22", "view": "P1.v2", "basis": "the section label"},
        {"key": "P1.u2", "can_answer": False, "answer": "should be ignored", "measurement": "99", "view": "P1.v1", "basis": "too small"},
        {"key": "P9.u9", "can_answer": True, "answer": "an invented key", "measurement": None, "view": None, "basis": ""}]})
    out = {s["key"]: s for s in suggest(llm, 1, b"png", questions, VIEWS, ["120", "22"])}
    assert set(out) == {"P1.u1", "P1.u2", "P1.u3"}                                   # the invented key is gone; the skipped one is still listed
    assert out["P1.u1"]["can_answer"] and out["P1.u1"]["measurement"] == "22" and out["P1.u1"]["view"] == "P1.v2"
    assert out["P1.u2"]["can_answer"] is False and out["P1.u2"]["answer"] == "" and out["P1.u2"]["measurement"] is None
    assert out["P1.u3"]["can_answer"] is False and "did not answer" in out["P1.u3"]["basis"]
    sent = json.loads(llm.calls[0][1][1])                                            # the model is told the real views and only the asked questions
    assert [q["key"] for q in sent["questions"]] == ["P1.u1", "P1.u2", "P1.u3"] and sent["views"][1]["id"] == "P1.v2"


def test_a_measurement_without_a_real_view_or_with_several_numbers_is_dropped_but_the_answer_stays():
    questions = [{"key": "P1.u1", "question": "q1"}, {"key": "P1.u2", "question": "q2"}]
    llm = Scripted({"suggestions": [
        {"key": "P1.u1", "can_answer": True, "answer": "It is 22.", "measurement": "22", "view": "P1.v77", "basis": "x"},
        {"key": "P1.u2", "can_answer": True, "answer": "Both numbers.", "measurement": "22 and 42", "view": "P1.v1", "basis": "y"}]})
    out = {s["key"]: s for s in suggest(llm, 1, b"png", questions, VIEWS, [])}
    assert out["P1.u1"]["answer"] == "It is 22." and out["P1.u1"]["measurement"] is None
    assert out["P1.u2"]["answer"] == "Both numbers." and out["P1.u2"]["measurement"] is None


def test_every_question_is_asked_even_when_there_are_more_than_one_batch():
    questions = [{"key": f"P1.u{i}", "question": f"q{i}"} for i in range(1, 46)]
    class Echo(Scripted):
        def generate_json(self, system, parts):
            asked = json.loads(parts[1])["questions"]
            self.data = {"suggestions": [{"key": q["key"], "can_answer": True, "answer": "ok", "measurement": None, "view": None, "basis": "b"} for q in asked]}
            return super().generate_json(system, parts)
    llm = Echo({})
    out = suggest(llm, 1, b"png", questions, VIEWS, [])
    assert len(llm.calls) == 3 and [s["key"] for s in out] == [q["key"] for q in questions] and all(s["can_answer"] for s in out)    # 20 + 20 + 5


def test_a_title_or_label_is_never_proposed_as_a_measurement():
    from app.drawing.clarify import looks_like_dimension
    assert looks_like_dimension("22") and looks_like_dimension("Ø10 - 8 hole") and looks_like_dimension("2X45°") and looks_like_dimension("R27")
    assert not looks_like_dimension("05. CONNECTING ROD CAP") and not looks_like_dimension("SCALE 1:1,5") and not looks_like_dimension("SECTION D-D")
    llm = Scripted({"suggestions": [{"key": "P1.u1", "can_answer": True, "answer": "It is the part title.", "measurement": "05. CONNECTING ROD CAP", "view": "P1.v1", "basis": "x"}]})
    out = suggest(llm, 1, b"png", [{"key": "P1.u1", "question": "q"}], VIEWS, [])
    assert out[0]["answer"] == "It is the part title." and out[0]["measurement"] is None


def test_a_reply_without_a_suggestions_list_is_refused_not_guessed():
    try:
        suggest(Scripted({"oops": 1}), 1, b"png", [{"key": "P1.u1", "question": "q"}], VIEWS, [])
        assert False, "a malformed reply was accepted"
    except ValueError:
        pass


def test_the_worker_action_is_allowed_and_server_returns_proposals_only():
    from app.agent.harness import ACTIONS
    from app.web import server
    assert "suggest_clarifications" in ACTIONS["extraction"]
    result = with_questions()
    m = reconcile(result)
    llm = Scripted({"suggestions": [{"key": "P1.u1", "can_answer": True, "answer": "It prints 22.", "measurement": "22", "view": "P1.v2", "basis": "b"}]})
    with patch.object(server, "result_with_design", lambda rid: {**result, "drawing_model": m.model_dump()}), \
         patch.object(server, "default_cache", lambda: type("C", (), {"get_blob": lambda self, sha: b"png"})()):
        out = server.clarify_suggestions("abcdef123456", client=llm)
    assert out["calls"] == 1 and out["model_hash"] == m.digest() and out["source_hash"] == m.source_hash
    assert {s["key"] for s in out["suggestions"]} == {"P1.u1", "P1.u2", "P1.u3"} and all(s["page"] == 1 for s in out["suggestions"])
    assert "Nothing is saved" in out["note"]
    assert reconcile(result).resolutions.clarifications == []                        # the model's own state is untouched


def test_the_ai_is_asked_only_the_questions_the_page_shows():
    result = with_questions()
    read = result["pages"][0]["read"]
    read["evidence"] = {"extraction": {"callouts": [{"id": "o1", "category": "measurement"}]}}
    read["issues"] = ["o1: conflict: 120", "No tolerances are printed on this sheet.", "Is the bore blind or through?"]
    m = reconcile(result)
    asked = open_questions(result, m)
    shown = [q for q in m.issues if "unreadable source callout" in q]
    assert [q["question"] for q in asked] == ["Is the bore blind or through?"] and len(asked) == len(shown) == 1


def test_a_question_that_points_at_a_view_is_asked_with_its_close_up_one_picture_per_call():
    import io
    from PIL import Image
    from app.drawing.clarify import question_box
    from app.drawing.model import is_question
    buf = io.BytesIO(); Image.new("RGB", (2000, 1000), "white").save(buf, "PNG"); png = buf.getvalue()
    page = {"no": 1, "width": 2000, "height": 1000, "boxes": [[0, 0, 900, 900], [1000, 100, 1600, 700]],
            "read": {"views": [{"no": 1, "label": "07. COUPLING HALF FRONT"}, {"no": 2, "label": "07. COUPLING HALF SIDE SECTION"}],
                     "evidence": {"extraction": {"features": [{"id": "f8", "views": ["v2"]}], "callouts": [{"id": "o3", "box": [100, 100, 200, 200]}]}}}}
    assert question_box(page, "f8: the callout is crowded") == [1000, 100, 1600, 700]
    assert question_box(page, "o3: which surface?") == [200, 100, 400, 200]                                   # 0..1000 -> page pixels
    assert question_box(page, "The Ø20 callout on the coupling side section is crowded") == [1000, 100, 1600, 700]   # by the view title
    assert question_box(page, "Is the projection first angle?") is None
    assert not is_question("OCR omitted several small dimensions and misread degree symbols", {})             # a note about our reader
    qs = [{"key": "P1.u1", "question": "f8: crowded", "box": [1000, 100, 1600, 700]}, {"key": "P1.u2", "question": "projection?", "box": None}]
    class Echo(Scripted):
        def generate_json(self, system, parts):
            assert sum(1 for p in parts if not isinstance(p, str)) == 1                                     # Domo takes one picture per call
            ctx = json.loads(parts[1])
            self.data = {"suggestions": [{"key": q["key"], "can_answer": True, "answer": ctx["picture"], "measurement": None, "view": None, "basis": "b"}
                                         for q in ctx["questions"]]}
            return super().generate_json(system, parts)
    llm = Echo({})
    out = suggest(llm, 1, png, qs, VIEWS, [])
    assert [(s["key"], s["answer"]) for s in out] == [("P1.u1", "close-up"), ("P1.u2", "whole page")]
    assert Image.open(io.BytesIO(llm.calls[0][1][0].data)).size == (840, 820)                               # the view + 120 margin (clipped at the top), full resolution


def test_a_question_raised_twice_is_asked_once_and_answers_both():
    from app.drawing.clarify import same_question
    a = "The Ø20 callout on the coupling side section is legible but its exact referenced cylindrical surface is visually crowded."
    b = "f8: The attachment of the Ø20 callout is crowded and should be verified against the source CAD if manufacturing-critical."
    assert same_question(a, b)
    assert not same_question(a, "The Ø30 callout on the coupling side section is crowded")                  # another value: another question
    assert not same_question("Is 42 the hole depth?", "Is 42 a typo for 22 in the side view?")              # one shared value is not enough
    result = drawing_result()
    result["pages"][0]["read"]["issues"] = [a, "Is the projection symbol first angle?", b]
    qs = open_questions(result, model())
    assert [(q["key"], q["also"]) for q in qs] == [("P1.u1", ["P1.u3"]), ("P1.u2", [])]
    out = suggest(Scripted({"suggestions": [{"key": "P1.u1", "can_answer": True, "answer": "Ø70", "measurement": None, "view": None, "basis": "b"},
                                            {"key": "P1.u2", "can_answer": False, "answer": "", "measurement": None, "view": None, "basis": "b"}]}),
                  1, b"png", qs, VIEWS, [])
    assert out[0]["also"] == ["P1.u3"]


def test_the_doubtful_callout_is_found_and_outlined_on_the_close_up():
    import io
    from PIL import Image
    from app.drawing.clarify import crop_png, question_mark
    page = {"no": 2, "width": 2000, "height": 1000, "boxes": [[0, 0, 900, 900], [1000, 100, 1600, 700]],
            "read": {"views": [{"no": 1, "label": "FRONT"}, {"no": 2, "label": "SIDE SECTION"}],
                     "evidence": {"extraction": {"callouts": [{"id": "o1", "text": "Ø20", "box": [100, 100, 150, 200], "views": ["v1"]},
                                                              {"id": "o2", "text": "Ø20", "box": [600, 300, 650, 400], "views": ["v2"]},
                                                              {"id": "o3", "text": "Ø205", "box": [700, 300, 750, 400], "views": ["v2"]}]}}}}
    assert question_mark(page, "The Ø20 callout is crowded", [1000, 100, 1600, 700]) == [1200, 300, 1300, 400]   # the one in the view asked about
    assert question_mark(page, "The Ø20 callout is crowded", None) is None                                      # two on the page: no guess
    assert question_mark(page, "o3: conflict", None) == [1400, 300, 1500, 400]
    buf = io.BytesIO(); Image.new("RGB", (2000, 1000), "white").save(buf, "PNG")
    crop = Image.open(io.BytesIO(crop_png(buf.getvalue(), [1000, 100, 1600, 700], mark=[1200, 300, 1300, 400])))
    assert crop.getpixel((1200 - 880 - 10, 350 - 0)) == (220, 30, 30)                                          # a red outline round the callout
