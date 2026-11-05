"""Offline checks of the phase P3 reader (whole page + packed crops, 2 calls a page): python tests/test_extract.py"""
import io

from PIL import Image

import fakes  # noqa: F401
from fakes import FakeModel

from app.perception.extract import CROPS_PROMPT, PAGE_PROMPT, MAX_SIDE, CropView, combine, pack, read_page


def page(w=1400, h=900) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "PNG")
    return buf.getvalue()


def test_pack_puts_all_crops_on_as_few_sheets_as_fit():
    sheets = pack(page(), [[0, 0, 300, 200], [400, 0, 900, 300], [100, 400, 600, 800]])
    assert len(sheets) == 1 and sheets[0][1] == [1, 2, 3]
    im = Image.open(io.BytesIO(sheets[0][0]))
    assert max(im.size) <= MAX_SIDE
    big = pack(page(3000, 3000), [[0, 0, 1900, 1900], [1000, 1000, 2900, 2900]])   # too big even at the smallest scale: two sheets
    assert [n for _, n in big] == [[1], [2]] and all(max(Image.open(io.BytesIO(s)).size) <= MAX_SIDE for s, _ in big)
    hi_res = pack(page(2600, 1900), [[0, 0, 1200, 800], [1300, 0, 2500, 800], [0, 900, 1200, 1800], [1300, 900, 2500, 1800]])
    assert len(hi_res) == 1, "a 300 DPI page's crops are shrunk (not below 60%) to fit ONE sheet = one call"
    with_page = pack(page(), [[0, 0, 300, 200], [400, 0, 900, 300]], overview=page())   # the overview sits on the sheet, not in the numbers
    assert len(with_page) == 1 and with_page[0][1] == [1, 2]
    assert Image.open(io.BytesIO(with_page[0][0])).height > 640


def test_combine_counts_each_callout_once_and_keeps_its_view():
    views = [CropView(1, [0, 0, 1, 1], dims=["Ø50", "R35", "R35"]), CropView(2, [0, 0, 1, 1], dims=["60"])]
    dims = combine(1, ["⌀50", "R35", "200"], views)
    from app.perception.dims import dim_tokens
    by = {}
    for d in dims:                                                      # keyed by the normalised value ("⌀50" and "Ø50" are one)
        by.setdefault(dim_tokens(d.text)[0], []).append(d)
    assert len(by["R35"]) == 1                                          # the page saw it once: printed once (one crop read it twice)
    overlap = combine(1, ["R20"], [CropView(1, [0, 0, 1, 1], dims=["R20"]), CropView(2, [0, 0, 1, 1], dims=["R20"])])
    assert len(overlap) == 1 and overlap[0].views == [1, 2]             # overlapping crops: one callout, seen in both crops
    crops_only = combine(1, [], [CropView(1, [0, 0, 1, 1], dims=["R2", "R2"]), CropView(2, [0, 0, 1, 1], dims=["R2"])])
    assert len(crops_only) == 2                                         # page missed it: the most any one crop saw
    assert by["Ø50"][0].views == [1] and not by["Ø50"][0].single_source  # both reads agree
    assert by["200"][0].views == [] and by["200"][0].single_source      # page only: unplaced, worth a look
    assert by["60"][0].views == [2] and by["60"][0].single_source       # crop only
    assert [d.id for d in dims] == [f"P1.d{i}" for i in range(1, len(dims) + 1)]


def test_read_page_makes_two_calls_and_uses_both_reads():
    model = FakeModel(reads=[{"d": ["200", "Ø50"], "tb": {"unit": "mm"}, "notes": ["all fillets R2"]},
                             {"c": {"1": {"t": "plan", "l": "", "sc": None, "s": None, "d": ["Ø50", "R35"]},
                                    "2": {"t": "plan", "s": 1, "d": ["32"]}}}])
    r = read_page(model, 1, page(), [[0, 0, 300, 200], [300, 0, 600, 200]])
    assert r.calls == 2 and [c[0] for c in model.calls] == ["other", "other"]
    assert {d.text for d in r.dims} == {"200", "Ø50", "R35", "32"}
    assert r.views[0].type == "plan" and r.views[1].same_as == 1 and r.title_block["unit"] == "mm" and r.notes == ["all fillets R2"]
    junk = FakeModel(reads=["not json", {"c": {"1": "nonsense", "9": {"d": ["5"]}}}])  # a bad reply never crashes; nothing invented
    r = read_page(junk, 1, page(), [[0, 0, 300, 200]])
    assert r.dims == [] and r.views[0].type == "none"


def test_prompts_stay_short():
    """Keep prompts bounded, including the explicit projection-symbol evidence."""
    assert len(PAGE_PROMPT) < 1500 and len(CROPS_PROMPT) < 1250, (len(PAGE_PROMPT), len(CROPS_PROMPT))


def test_large_crop_with_overview_stays_within_domo_image_limit():
    sheets = pack(page(3500, 3500), [[0, 0, 3400, 3400]], overview=page(3500, 3500))
    assert all(numbers for _, numbers in sheets), "An overview-only image must not create a paid crop call"
    assert [n for _, numbers in sheets for n in numbers] == [1]
    assert all(max(Image.open(io.BytesIO(data)).size) <= MAX_SIDE for data, _ in sheets)


def test_invalid_page_or_missing_crop_reply_is_not_sticky_in_the_cache():
    import tempfile
    from app.cache import Cache
    from app.llm.cached import CachedClient
    source = page()
    fake = FakeModel(reads=[{}, {"d": ["10"], "tb": {}, "notes": []}])
    client = CachedClient(fake, Cache(tempfile.mkdtemp()))
    assert not read_page(client, 1, source, []).dims
    assert read_page(client, 1, source, []).dims and len(fake.calls) == 2
    fake = FakeModel(reads=[{"d": ["10"], "tb": {}, "notes": []}, {"c": {}},
                            {"c": {"1": {"t": "plan", "d": ["10"]}}}])
    client = CachedClient(fake, Cache(tempfile.mkdtemp()))
    boxes = [[0, 0, 300, 200]]
    assert read_page(client, 1, source, boxes).dims[0].single_source
    result = read_page(client, 1, source, boxes)
    assert not result.dims[0].single_source and len(fake.calls) == 3


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
