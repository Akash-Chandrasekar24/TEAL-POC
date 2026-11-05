"""Geometry checks for the placement foundation; no SolidWorks required."""
import math

import fakes  # noqa: F401

from app.features.base import FeatureRequestError, PlaneFrame, Position
from app.features.cut_feature import CutFeature, CutShape
from app.features.face_sketch import FaceFrame
from app.features.frames import SketchFrame


def close(a, b, tol=1e-10):
    assert len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b)), (a, b)


def refused(fn):
    try:
        fn()
    except FeatureRequestError:
        return
    raise AssertionError("invalid frame was accepted")


def test_rotated_translated_frame_roundtrips_in_both_units():
    # An inclined plane with an asymmetric point exercises all world axes.
    c, s = math.cos(0.63), math.sin(0.63)
    for scale in (1, 0.001, 0.0254):
        frame = SketchFrame(tuple(v * scale for v in (17, -9, 23)), (c, s, 0), (0, 0, 1))
        local = tuple(v * scale for v in (3, -7, 11))
        close(frame.to_local(frame.to_world(*local)), local)
        world = frame.to_world(3 * scale, -7 * scale)
        close(frame.project(world, 1e-9), (3 * scale, -7 * scale))
        refused(lambda: frame.project(frame.to_world(0, 0, scale), scale / 2))


def test_datum_frames_match_existing_measured_conventions():
    expected = {"front": (2, 3, 5), "top": (2, 5, -3), "right": (5, 3, -2)}
    for datum, world in expected.items():
        plane = PlaneFrame(datum)
        frame = plane.sketch_frame(5)
        close(frame.to_world(2, 3), world)
        close(frame.to_local(world), (2, 3, 0))


def test_bad_frames_and_off_plane_readbacks_are_refused():
    for origin, x, y in [
        ((0, 0), (1, 0, 0), (0, 1, 0)),
        ((0, 0, float("nan")), (1, 0, 0), (0, 1, 0)),
        ((0, 0, 0), (0, 0, 0), (0, 1, 0)),
        ((0, 0, 0), (2, 0, 0), (0, 1, 0)),
        ((0, 0, 0), (1, 0, 0), (1, 0, 0)),
        ((0, 0, 0), (float("inf"), 0, 0), (0, 1, 0)),
    ]:
        refused(lambda: SketchFrame(origin, x, y))
    refused(lambda: FaceFrame([0, 0, .02], [1, 0, 0], [0, 1, 0], PlaneFrame("front"), .01))
    refused(lambda: FaceFrame([0, 0, .01], [1, 0, 0], [0, 0, 1], PlaneFrame("front"), .01))
    tilt = .001
    refused(lambda: FaceFrame([0, 0, .01], [1, 0, 0], [0, math.cos(tilt), math.sin(tilt)], PlaneFrame("front"), .01))


def test_rounded_readback_directions_remain_invertible():
    frame = SketchFrame((0, 0, 0), (.707107, .707107, 0), (-.707107, .707107, 0))
    close(frame.to_local(frame.to_world(3, -8, 2)), (3, -8, 2))


def test_mirrored_face_records_preserve_world_geometry_and_arc_sweep():
    for datum in ("front", "top", "right"):
        source = PlaneFrame(datum).sketch_frame(.01)
        for mirror in (1, -1):
            # Rotate 90 degrees in-plane and optionally reflect the face sketch.
            xdir = source.ydir
            ydir = tuple(-mirror * v for v in source.xdir)
            origin = source.to_world(.004, -.006)
            target = SketchFrame(origin, xdir, ydir)
            face = FaceFrame(origin, xdir, ydir, PlaneFrame(datum), .01)
            for direction in (1, -1):
                for end in ((0, 1), (-1, 0), (0, -1), (1, 0)):
                    arc = {"kind": "arc", "cx": 0, "cy": 0, "x1": 1, "y1": 0,
                           "x2": end[0], "y2": end[1], "dir": direction}
                    record = face.records([arc])[0]
                    assert record["direction"] == mirror * direction
                    close(target.to_world(record["x1"], record["y1"]), source.to_world(1, 0))
                    close(target.to_world(record["x2"], record["y2"]), source.to_world(*end))
            circle = face.records([{"kind": "circle", "cx": .012, "cy": -.007, "diameter": .008}])[0]
            assert circle["radius"] == .004
            close(target.to_world(circle["cx"], circle["cy"]), source.to_world(.012, -.007))


def test_cut_face_mapping_uses_drawing_units_and_shared_validation():
    for unit, scale in (("mm", .001), ("inch", .0254)):
        cut = CutFeature(unit=unit, shapes=[CutShape(kind="circle", position=Position(3, -2), diameter=4)],
                         plane="front", offset=5, depth=1)
        origin = [scale, 0, 5 * scale]
        records = cut.entities_in_face_frame(origin, [-1, 0, 0], [0, 1, 0], PlaneFrame("front"))
        close((records[0]["cx"], records[0]["cy"], records[0]["radius"]), (-2 * scale, -2 * scale, 2 * scale))
        refused(lambda: cut.entities_in_face_frame([0, 0, 0], [1, 0, 0], [0, 1, 0], PlaneFrame("front")))


def test_slot_cut_preserves_endpoints_radii_and_sense_on_mirrored_face():
    cut = CutFeature(unit="mm", plane="front", offset=10, depth=2,
                     shapes=[CutShape(kind="slot", position=Position(7, -3), centre_distance=12, width=4, angle_deg=37)])
    source = PlaneFrame("front").sketch_frame(.01)
    target = SketchFrame((.002, -.005, .01), (0, -1, 0), (-1, 0, 0))
    records = cut.entities_in_face_frame(target.origin, target.xdir, target.ydir, PlaneFrame("front"))
    segments = [s for shape in cut.shape_segments_m() for s in shape]
    assert len(records) == len(segments) == 4
    for segment, record in zip(segments, records):
        for x, y in (("x1", "y1"), ("x2", "y2")):
            close(target.to_world(record[x], record[y]), source.to_world(segment[x], segment[y]))
        if segment["kind"] == "arc":
            assert record["direction"] == -segment["dir"]
            close(target.to_world(record["cx"], record["cy"]), source.to_world(segment["cx"], segment["cy"]))
