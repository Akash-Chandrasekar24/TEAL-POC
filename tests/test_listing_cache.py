"""Face / edge listings are cached until any other tool is called (they took 10-60 s each and were asked twice at the same state)."""
import asyncio
from types import SimpleNamespace

import fakes  # noqa: F401
from app.mcp_client.solidworks import SolidWorksClient


def test_face_and_edge_listings_are_reused_until_the_model_may_change():
    sent = []

    class Session:
        async def call_tool(self, tool, args, read_timeout_seconds=None):
            sent.append((tool, args.get("analysis_type")))
            return SimpleNamespace(content=[SimpleNamespace(text=f"COMPLETED | {tool} {len(sent)}")], is_error=False)

    sw = SolidWorksClient.__new__(SolidWorksClient)
    sw._session, sw.tool_names, sw._log = Session(), [], lambda *a: None

    async def run():
        a = await sw.analyze_model("faces"); b = await sw.analyze_model("faces")
        await sw.analyze_model("geometry")                                    # a measurement does not change the model
        c = await sw.analyze_model("faces")
        await sw.call("extrude_feature", {"depth": 0.01})                    # this may change it
        d = await sw.analyze_model("faces")
        e = await sw.analyze_model("faces", near=[0, 0, 0])                  # a filtered query is never cached
        return a, b, c, d, e

    a, b, c, d, e = asyncio.run(run())
    assert a is b is c and d is not a and e is not d
    assert [t for t in sent if t[1] == "faces"] == [("analyze_model", "faces")] * 3
