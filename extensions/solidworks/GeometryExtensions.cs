// Application-owned geometry operations the pinned upstream bridge does not offer: swept CUT (and swept boss in one call),
// shell, a reference plane at an angle about an axis, and a sketch + extrude on any plane. Every coordinate arrives in MODEL
// space (metres) and is converted into the sketch's own frame with SolidWorks' ModelToSketchTransform, so no sketch-frame
// convention is assumed. Never starts SolidWorks: it attaches to the running instance, on the STA thread.
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Web.Http;
using Newtonsoft.Json.Linq;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using SolidworksExecution.Infrastructure;

namespace DrawingToSolidworks
{
    public sealed class GeometryExtensionsController : ApiController
    {
        [HttpPost, Route("api/ext/sweep")]
        public IHttpActionResult Sweep([FromBody] JObject body) { return Run(() => DoSweep(body)); }

        [HttpPost, Route("api/ext/shell")]
        public IHttpActionResult Shell([FromBody] JObject body) { return Run(() => DoShell(body)); }

        [HttpPost, Route("api/ext/angled_plane")]
        public IHttpActionResult AngledPlane([FromBody] JObject body) { return Run(() => DoAngledPlane(body)); }

        [HttpPost, Route("api/ext/sketch_extrude")]
        public IHttpActionResult SketchExtrude([FromBody] JObject body) { return Run(() => DoSketchExtrude(body)); }

        // Nothing selected before a save / export: SolidWorks writes an STL of the SELECTED body only (measured: a shaft exported as its lobe).
        [HttpPost, Route("api/ext/clear_selection")]
        public IHttpActionResult ClearSelection([FromBody] JObject body)
        {
            return Run(() =>
            {
                var model = App().IActiveDoc2 as IModelDoc2;
                if (model == null) throw new InvalidOperationException("No active document");
                model.ClearSelection2(true);
                return new JObject { ["ok"] = true, ["selected"] = ((ISelectionMgr)model.SelectionManager).GetSelectedObjectCount2(-1) };
            });
        }

        // ISldWorks.CommandInProgress: SolidWorks serves an out-of-process caller's COM calls at once instead of between its idle cycles.
        // Measured on this machine: 56-75 ms per call off, 2.3 ms on. Switched on for a build and OFF at its end (the UI waits while it is on).
        [HttpPost, Route("api/ext/command_in_progress")]
        public IHttpActionResult CommandInProgress([FromBody] JObject body)
        {
            return Run(() =>
            {
                bool on = body != null && body.Value<bool?>("on") == true;
                App().CommandInProgress = on;
                return new JObject { ["ok"] = true, ["on"] = on };
            });
        }

        // The EXACT world bounding box of the part's solid bodies: the extreme point of every body along +-X, +-Y, +-Z (6 calls per body).
        // Before: every face and edge was listed to compute it (a 42-tooth gear: ~850 faces, over 120 s).
        [HttpPost, Route("api/ext/bbox")]
        public IHttpActionResult BBox([FromBody] JObject body)
        {
            return Run(() =>
            {
                var model = Part();
                var bodies = ((IPartDoc)model).GetBodies2((int)swBodyType_e.swSolidBody, true) as object[];
                if (bodies == null || bodies.Length == 0) return new JObject { ["ok"] = true, ["bodies"] = 0 };
                double[] lo = { double.MaxValue, double.MaxValue, double.MaxValue }, hi = { double.MinValue, double.MinValue, double.MinValue };
                foreach (object o in bodies)
                {
                    var b = (IBody2)o;
                    for (int axis = 0; axis < 3; axis++)
                        foreach (int sign in new[] { -1, 1 })
                        {
                            double[] d = { 0, 0, 0 }; d[axis] = sign;
                            double x, y, z;
                            if (!b.GetExtremePoint(d[0], d[1], d[2], out x, out y, out z)) throw new InvalidOperationException("GetExtremePoint failed");
                            double v = axis == 0 ? x : axis == 1 ? y : z;
                            if (sign < 0) lo[axis] = Math.Min(lo[axis], v); else hi[axis] = Math.Max(hi[axis], v);
                        }
                }
                return new JObject { ["ok"] = true, ["bodies"] = bodies.Length, ["lo"] = new JArray(lo[0], lo[1], lo[2]), ["hi"] = new JArray(hi[0], hi[1], hi[2]) };
            });
        }

        private IHttpActionResult Run(Func<JObject> work)
        {
            try { return Ok(StaExecutor.Instance.Run(work)); }
            catch (Exception ex) { return Content(System.Net.HttpStatusCode.Conflict, new { error = ex.Message }); }
        }

        // ------------------------------------------------------------------ document and planes
        private static ISldWorks App() { return (ISldWorks)Marshal.GetActiveObject("SldWorks.Application"); }

        private static IModelDoc2 Part()
        {
            var model = App().IActiveDoc2 as IModelDoc2;
            if (model == null || model.GetType() != (int)swDocumentTypes_e.swDocPART) throw new InvalidOperationException("The active document must be a part");
            return model;
        }

        private static IFeature DefaultPlane(IModelDoc2 model, string datum)
        {
            int ordinal = datum == "front" ? 0 : datum == "top" ? 1 : datum == "right" ? 2 : -1;
            if (ordinal < 0) throw new InvalidOperationException("datum must be front, top or right");
            int seen = 0;
            foreach (var o in (model.FeatureManager.GetFeatures(true) as object[]) ?? new object[0])
            {
                var f = o as IFeature;
                if (f == null || f.GetTypeName2() != "RefPlane") continue;
                if (seen++ == ordinal) return f;
            }
            throw new InvalidOperationException("default plane not found: " + datum);
        }

        private static IFeature LastFeature(IModelDoc2 model)
        {
            var all = model.FeatureManager.GetFeatures(true) as object[];
            return all != null && all.Length > 0 ? all[all.Length - 1] as IFeature : null;
        }

        private static int FeatureCount(IModelDoc2 model)
        {
            var all = model.FeatureManager.GetFeatures(true) as object[];
            return all == null ? 0 : all.Length;
        }

        /// <summary>{datum, offset} (a default plane, optionally offset along its +normal) or {name} (a plane made earlier). Selects it with `mark`.</summary>
        private static void SelectPlane(IModelDoc2 model, JObject plane, bool append, int mark)
        {
            if (plane == null) throw new InvalidOperationException("plane is required");
            string name = plane.Value<string>("name");
            IFeature datum;
            if (!string.IsNullOrEmpty(name))
            {
                datum = ((IPartDoc)model).FeatureByName(name) as IFeature;
                if (datum == null) throw new InvalidOperationException("plane not found: " + name);
            }
            else datum = DefaultPlane(model, plane.Value<string>("datum"));
            double offset = plane.Value<double?>("offset") ?? 0.0;
            if (Math.Abs(offset) < 1e-12)
            {
                if (!append) model.ClearSelection2(true);
                if (!datum.Select2(append, mark)) throw new InvalidOperationException("could not select the datum plane");
                return;
            }
            model.ClearSelection2(true);
            datum.Select2(false, 0);
            // InsertRefPlane's distance is unsigned: the minus side needs OptionFlip (as upstream found live)
            int c = (int)swRefPlaneReferenceConstraints_e.swRefPlaneReferenceConstraint_Distance;
            if (offset < 0) c |= (int)swRefPlaneReferenceConstraints_e.swRefPlaneReferenceConstraint_OptionFlip;
            var made = model.FeatureManager.InsertRefPlane(c, Math.Abs(offset), 0, 0, 0, 0) as IFeature;
            if (made == null) throw new InvalidOperationException("could not make the offset plane");
            if (!append) model.ClearSelection2(true);
            if (!made.Select2(append, mark)) throw new InvalidOperationException("could not select the offset plane");
        }

        // ------------------------------------------------------------------ sketches in model coordinates
        private static double[] P(JToken t)
        {
            var a = t as JArray;
            if (a == null || a.Count != 3) throw new InvalidOperationException("a point is [x, y, z] in metres");
            var p = new double[] { a[0].Value<double>(), a[1].Value<double>(), a[2].Value<double>() };
            foreach (var v in p) if (Double.IsNaN(v) || Double.IsInfinity(v)) throw new InvalidOperationException("non-finite coordinate");
            return p;
        }

        private sealed class Frame
        {
            private readonly IMathUtility _math; private readonly MathTransform _m2s;
            public Frame(ISketch sketch) { _math = (IMathUtility)App().GetMathUtility(); _m2s = sketch.ModelToSketchTransform; }
            public double[] ToSketch(double[] model)
            {
                var pt = (IMathPoint)_math.CreatePoint(model);
                var r = ((IMathPoint)pt.MultiplyTransform(_m2s)).ArrayData as double[];
                return r;
            }
        }

        /// <summary>Draw `entities` (model coordinates) in a new sketch on `plane`; returns the sketch's name (the sketch is closed).</summary>
        private static string DrawSketch(IModelDoc2 model, JObject plane, JArray entities)
        {
            SelectPlane(model, plane, false, 0);
            model.SketchManager.InsertSketch(true);
            var sketch = model.SketchManager.ActiveSketch;
            if (sketch == null) throw new InvalidOperationException("could not open a sketch on the plane");
            var frame = new Frame(sketch);
            model.SketchManager.AddToDB = true;                        // exact coordinates: no snapping or inferred relations
            model.SketchManager.DisplayWhenAdded = false;              // no redraw per entity (a gear outline is 840 entities)
            var view = model.ActiveView as IModelView;
            if (view != null) view.EnableGraphicsUpdate = false;
            try
            {
                foreach (JObject e in entities)
                {
                    string kind = e.Value<string>("kind");
                    object made;
                    if (kind == "line")
                    {
                        var a = frame.ToSketch(P(e["p1"])); var b = frame.ToSketch(P(e["p2"]));
                        made = model.SketchManager.CreateLine(a[0], a[1], 0, b[0], b[1], 0);
                    }
                    else if (kind == "circle")
                    {
                        var c = frame.ToSketch(P(e["center"]));
                        made = model.SketchManager.CreateCircleByRadius(c[0], c[1], 0, e.Value<double>("radius"));
                    }
                    else if (kind == "arc")
                    {
                        // centre + start + end + a point ON the arc between them: the turning sense is decided in the sketch's own frame
                        var c = frame.ToSketch(P(e["center"])); var a = frame.ToSketch(P(e["p1"]));
                        var b = frame.ToSketch(P(e["p2"])); var m = frame.ToSketch(P(e["mid"]));
                        double a1 = Math.Atan2(a[1] - c[1], a[0] - c[0]), a2 = Math.Atan2(b[1] - c[1], b[0] - c[0]), am = Math.Atan2(m[1] - c[1], m[0] - c[0]);
                        double ccwEnd = Norm(a2 - a1), ccwMid = Norm(am - a1);
                        short dir = (short)(ccwMid < ccwEnd ? 1 : -1);
                        made = model.SketchManager.CreateArc(c[0], c[1], 0, a[0], a[1], 0, b[0], b[1], 0, dir);
                    }
                    else throw new InvalidOperationException("sketch entity kind must be line, arc or circle");
                    if (made == null) throw new InvalidOperationException("SolidWorks refused a sketch " + kind);
                }
            }
            finally
            {
                model.SketchManager.AddToDB = false;
                model.SketchManager.DisplayWhenAdded = true;
                if (view != null) view.EnableGraphicsUpdate = true;
            }
            string name = ((IFeature)sketch).Name;
            model.SketchManager.InsertSketch(true);
            return name;
        }

        private static double Norm(double a) { a %= 2 * Math.PI; return a < 0 ? a + 2 * Math.PI : a; }

        private static JObject Done(IModelDoc2 model, IFeature feature, int before)
        {
            if (feature == null || FeatureCount(model) <= before) throw new InvalidOperationException("SolidWorks did not create the feature");
            return new JObject { ["ok"] = true, ["feature"] = feature.Name, ["rebuild_ok"] = model.EditRebuild3() };
        }

        // ------------------------------------------------------------------ operations
        private static JObject DoSweep(JObject body)
        {
            var model = Part();
            string op = body.Value<string>("operation");
            if (op != "boss" && op != "cut") throw new InvalidOperationException("operation must be boss or cut");
            var path = (JObject)body["path"]; var profile = (JObject)body["profile"];
            string pathName = DrawSketch(model, (JObject)path["plane"], (JArray)path["entities"]);
            string profileName = DrawSketch(model, (JObject)profile["plane"], (JArray)profile["entities"]);
            model.ClearSelection2(true);
            if (!model.Extension.SelectByID2(profileName, "SKETCH", 0, 0, 0, false, 1, null, 0)) throw new InvalidOperationException("profile sketch not selectable");
            if (!model.Extension.SelectByID2(pathName, "SKETCH", 0, 0, 0, true, 4, null, 0)) throw new InvalidOperationException("path sketch not selectable");
            int before = FeatureCount(model);
            IFeature f = op == "boss"
                ? model.FeatureManager.InsertProtrusionSwept4(false, false, 0, false, false, 0, 0, false, 0.0, 0.0, 0, 0,
                                                              true, true, true, 0.0, false, false, 0.0, 0)
                : model.FeatureManager.InsertCutSwept5(false, false, 0, false, false, 0, 0, false, 0.0, 0.0, 0, 0,
                                                       true, true, 0.0, false, false, false, false, false, 0.0, 0);
            var result = Done(model, f, before);
            result["path_sketch"] = pathName; result["profile_sketch"] = profileName;
            return result;
        }

        private static JObject DoShell(JObject body)
        {
            var model = Part();
            double t = body.Value<double>("thickness");
            if (!(t > 0)) throw new InvalidOperationException("thickness must be positive");
            var faces = body["faces"] as JArray;
            if (faces == null || faces.Count == 0) throw new InvalidOperationException("faces: at least one face to remove, as a point on it");
            model.ClearSelection2(true);
            bool append = false;
            foreach (var f in faces)
            {
                var p = P(f);
                if (!model.Extension.SelectByID2("", "FACE", p[0], p[1], p[2], append, 1, null, 0))
                    throw new InvalidOperationException(string.Format("no face at ({0}, {1}, {2})", p[0], p[1], p[2]));
                append = true;
            }
            int before = FeatureCount(model);
            model.InsertFeatureShell(t, body.Value<bool?>("outward") ?? false);
            return Done(model, LastFeature(model), before);
        }

        private static JObject DoAngledPlane(JObject body)
        {
            // The axis: a line through `point` along a world axis, made as the intersection of two offset default planes.
            var model = Part();
            var p = P(body["point"]);
            string along = body.Value<string>("along");
            string[] pair = along == "x" ? new[] { "top", "front" } : along == "y" ? new[] { "front", "right" } : along == "z" ? new[] { "top", "right" } : null;
            if (pair == null) throw new InvalidOperationException("along must be x, y or z");
            Func<string, double> coord = d => d == "front" ? p[2] : d == "top" ? p[1] : p[0];   // each default plane's normal coordinate
            SelectPlane(model, new JObject { ["datum"] = pair[0], ["offset"] = coord(pair[0]) }, false, 0);
            var first = SelectedPlaneName(model);
            SelectPlane(model, new JObject { ["datum"] = pair[1], ["offset"] = coord(pair[1]) }, false, 0);
            var second = SelectedPlaneName(model);
            model.ClearSelection2(true);
            model.Extension.SelectByID2(first, "PLANE", 0, 0, 0, false, 0, null, 0);
            model.Extension.SelectByID2(second, "PLANE", 0, 0, 0, true, 1, null, 0);
            if (!model.InsertAxis2(true)) throw new InvalidOperationException("could not make the axis");
            var axis = LastFeature(model);
            // The angled plane: at `angle_deg` to the reference plane, through the axis. SolidWorks may tilt it either way:
            // read its real normal and, if it is not the requested one, rebuild it with the flip option.
            var want = P(body["normal"]);
            double wl = Math.Sqrt(want[0] * want[0] + want[1] * want[1] + want[2] * want[2]);
            foreach (bool flip in new[] { false, true })
            {
                model.ClearSelection2(true);
                SelectPlane(model, (JObject)body["reference"], false, 0);
                if (!axis.Select2(true, 1)) throw new InvalidOperationException("could not select the axis");
                int before = FeatureCount(model);
                int angleC = (int)swRefPlaneReferenceConstraints_e.swRefPlaneReferenceConstraint_Angle;
                if (flip) angleC |= (int)swRefPlaneReferenceConstraints_e.swRefPlaneReferenceConstraint_OptionFlip;
                var plane = model.FeatureManager.InsertRefPlane(angleC, body.Value<double>("angle_deg") * Math.PI / 180.0,
                    (int)swRefPlaneReferenceConstraints_e.swRefPlaneReferenceConstraint_Coincident, 0, 0, 0) as IFeature;
                var result = Done(model, plane, before);
                var n = PlaneNormal(model, plane.Name);
                double cos = (n[0] * want[0] + n[1] * want[1] + n[2] * want[2]) / wl;
                if (Math.Abs(cos) > 0.9999)
                {
                    result["plane"] = plane.Name; result["axis"] = axis.Name; result["normal_sign"] = Math.Sign(cos); result["flipped"] = flip;
                    return result;
                }
                model.ClearSelection2(true);
                plane.Select2(false, 0);
                model.EditDelete();
            }
            throw new InvalidOperationException("the angled plane does not come out perpendicular to the requested direction");
        }

        /// <summary>A plane's +normal in model space, read through a temporary sketch on it (the proven sketch-frame layout), which is then deleted.</summary>
        private static double[] PlaneNormal(IModelDoc2 model, string planeName)
        {
            SelectPlane(model, new JObject { ["name"] = planeName }, false, 0);
            model.SketchManager.InsertSketch(true);
            var sketch = model.SketchManager.ActiveSketch;
            if (sketch == null) throw new InvalidOperationException("cannot open a sketch on the new plane");
            string name = ((IFeature)sketch).Name;
            model.SketchManager.InsertSketch(true);
            var n = SketchNormal(model, name);
            model.ClearSelection2(true);
            if (model.Extension.SelectByID2(name, "SKETCH", 0, 0, 0, false, 0, null, 0)) model.EditDelete();
            return n;
        }

        private static string SelectedPlaneName(IModelDoc2 model)
        {
            var sel = model.SelectionManager as ISelectionMgr;
            var f = sel?.GetSelectedObject6(1, -1) as IFeature;
            if (f == null) throw new InvalidOperationException("no plane selected");
            return f.Name;
        }

        private static JObject DoSketchExtrude(JObject body)
        {
            var model = Part();
            string op = body.Value<string>("operation");
            if (op != "boss" && op != "cut") throw new InvalidOperationException("operation must be boss or cut");
            string sketch = DrawSketch(model, (JObject)body["plane"], (JArray)body["entities"]);
            model.ClearSelection2(true);
            if (!model.Extension.SelectByID2(sketch, "SKETCH", 0, 0, 0, false, 0, null, 0)) throw new InvalidOperationException("sketch not selectable");
            bool through = body.Value<string>("end") == "through_all";
            int end = (int)(through ? swEndConditions_e.swEndCondThroughAll : swEndConditions_e.swEndCondBlind);
            double depth = body.Value<double?>("depth") ?? 0.0;
            if (!through && !(depth > 0)) throw new InvalidOperationException("a blind extrude needs a positive depth");
            // Direction: the caller gives the MODEL-space direction the material must grow / the cut must go; the sketch normal decides the flag.
            var dir = P(body["direction"]);
            var n = SketchNormal(model, sketch);
            bool reverse = dir[0] * n[0] + dir[1] * n[1] + dir[2] * n[2] < 0;
            double depth2 = body.Value<double?>("depth_back") ?? 0.0;             // > 0: also extrude this far the other way (double-ended)
            bool single = !(depth2 > 0);
            int before = FeatureCount(model);
            IFeature f = op == "boss"
                ? model.FeatureManager.FeatureExtrusion3(single, false, reverse, end, single ? 0 : (int)swEndConditions_e.swEndCondBlind, depth, depth2, false, false, false, false, 0, 0,
                                                         false, false, false, false, true, true, true, (int)swStartConditions_e.swStartSketchPlane, 0, false) as IFeature
                : model.FeatureManager.FeatureCut4(true, false, reverse, end, 0, depth, 0, false, false, false, false, 0, 0,
                                                   false, false, false, false, false, true, true, false, false, false,
                                                   (int)swStartConditions_e.swStartSketchPlane, 0, false, false) as IFeature;
            var result = Done(model, f, before);
            result["sketch"] = sketch;
            result["reversed"] = reverse;
            return result;
        }

        /// <summary>The sketch's +normal in model space (third axis of its sketch-to-model transform).</summary>
        private static double[] SketchNormal(IModelDoc2 model, string sketchName)
        {
            var feat = ((IPartDoc)model).FeatureByName(sketchName) as IFeature;
            var sk = feat?.GetSpecificFeature2() as ISketch;
            var s2m = sk?.ModelToSketchTransform?.IInverse();
            var d = s2m?.ArrayData as double[];
            if (d == null || d.Length < 9) throw new InvalidOperationException("cannot read the sketch plane");
            // the sketch-to-model transform's ArrayData: x axis d[0..2], y axis d[3..5], normal d[6..8] (upstream proved this layout live)
            return new double[] { d[6], d[7], d[8] };
        }
    }
}
