// Application-owned read/verification endpoint. Compiled into the BUILD COPY of
// upstream; the pinned checkout stays unchanged. No CreateObject/COM startup.
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
    public sealed class AssemblyInspectionController : ApiController
    {
        [HttpGet, Route("api/assembly/inspect")]
        public IHttpActionResult Inspect(bool workspace = false)
        {
            try { return Ok(StaExecutor.Instance.Run(() => Read(workspace))); }
            catch (Exception ex) { return Content(System.Net.HttpStatusCode.Conflict, new { error = ex.Message }); }
        }

        private static JObject Read(bool workspace)
        {
            var sw = (ISldWorks)Marshal.GetActiveObject("SldWorks.Application");
            var result = new JObject();
            var docs = new JArray();
            var doc = sw.GetFirstDocument() as IModelDoc2;
            while (doc != null)
            {
                docs.Add(new JObject { ["title"] = doc.GetTitle(), ["path"] = doc.GetPathName() });
                doc = doc.GetNext() as IModelDoc2;
            }
            result["documents"] = docs;
            var model = sw.IActiveDoc2 as IModelDoc2;
            result["title"] = model == null ? "" : model.GetTitle();
            result["active_path"] = model == null ? "" : model.GetPathName();
            result["modified"] = model != null && model.GetSaveFlag();
            result["configuration"] = model == null ? "" : model.ConfigurationManager.ActiveConfiguration.Name;
            if (workspace) return result;
            var asm = model as IAssemblyDoc;
            if (asm == null) throw new InvalidOperationException("Active document must be an assembly");
            result["rebuild_ok"] = model.EditRebuild3();
            var errors = new JArray();
            ReadErrors(model.FirstFeature() as IFeature, errors, false, 0);
            result["feature_errors"] = errors;
            var components = new JArray();
            var min = new[] { double.PositiveInfinity, double.PositiveInfinity, double.PositiveInfinity };
            var max = new[] { double.NegativeInfinity, double.NegativeInfinity, double.NegativeInfinity };
            foreach (var value in asm.GetComponents(true) as object[] ?? new object[0])
            {
                var comp = (IComponent2)value;
                if (comp.IsSuppressed()) throw new InvalidOperationException("Suppressed component: " + comp.Name2);
                if (comp.IGetChildrenCount() != 0) throw new InvalidOperationException("Nested assembly requires a flat leaf plan");
                var part = comp.GetModelDoc2() as IModelDoc2;
                if (part == null || part.Extension.ListExternalFileReferencesCount() != 0)
                    throw new InvalidOperationException("Unresolved component or external model references: " + comp.Name2);
                components.Add(new JObject { ["name"] = comp.Name2, ["fixed"] = comp.IsFixed(),
                    ["constrained_status"] = comp.GetConstrainedStatus() });
                object info;
                var bodies = comp.GetBodies3((int)swBodyType_e.swSolidBody, out info) as object[];
                if (bodies == null || bodies.Length == 0) throw new InvalidOperationException("No resolved solid bodies: " + comp.Name2);
                foreach (var item in bodies)
                {
                    var copy = ((IBody2)item).Copy() as IBody2;
                    if (copy == null) throw new InvalidOperationException("Cannot copy body for bounds");
                    try
                    {
                        if (!copy.ApplyTransform(comp.Transform2)) throw new InvalidOperationException("Cannot transform body for bounds");
                        for (int axis = 0; axis < 3; axis++)
                        {
                            foreach (int sign in new[] { -1, 1 })
                            {
                                double x, y, z;
                                if (!copy.GetExtremePoint(axis == 0 ? sign : 0, axis == 1 ? sign : 0,
                                                          axis == 2 ? sign : 0, out x, out y, out z))
                                    throw new InvalidOperationException("Body extreme-point calculation failed");
                                double coordinate = new[] { x, y, z }[axis];
                                min[axis] = Math.Min(min[axis], coordinate);
                                max[axis] = Math.Max(max[axis], coordinate);
                            }
                        }
                    }
                    finally { Marshal.ReleaseComObject(copy); }
                }
            }
            if (components.Count == 0) throw new InvalidOperationException("Empty assembly");
            result["constraints"] = components;
            result["bounds_m"] = new JArray(min[0], min[1], min[2], max[0], max[1], max[2]);
            var interferences = new JArray();
            var mgr = asm.InterferenceDetectionManager;
            if (mgr == null) throw new InvalidOperationException("Interference detection unavailable");
            try
            {
                mgr.TreatCoincidenceAsInterference = false;
                mgr.TreatSubAssembliesAsComponents = false;
                mgr.IncludeMultibodyPartInterferences = true;
                mgr.IgnoreHiddenBodies = false;
                mgr.ShowIgnoredInterferences = true;
                var found = mgr.GetInterferences() as object[] ?? new object[0];
                if (mgr.GetInterferenceCount() != found.Length)
                    throw new InvalidOperationException("Incomplete interference readback");
                foreach (var value in found)
                {
                    var interference = (IInterference)value;
                    var names = new JArray();
                    foreach (var c in interference.Components as object[] ?? new object[0]) names.Add(((IComponent2)c).Name2);
                    interferences.Add(new JObject { ["components"] = names, ["volume_m3"] = interference.Volume,
                        ["possible"] = interference.IsPossibleInterference });
                }
                result["interference_complete"] = true;
                result["interferences"] = interferences;
            }
            finally { mgr.Done(); }
            return result;
        }

        private static void ReadErrors(IFeature feature, JArray errors, bool subfeatures, int depth)
        {
            if (depth > 30) throw new InvalidOperationException("Feature tree depth exceeded");
            int count = 0;
            while (feature != null)
            {
                if (++count > 10000) throw new InvalidOperationException("Feature tree traversal incomplete");
                bool warning;
                int code = feature.GetErrorCode2(out warning);
                if (code != 0 && !warning) errors.Add(new JObject { ["name"] = feature.Name, ["code"] = code });
                ReadErrors(feature.GetFirstSubFeature() as IFeature, errors, true, depth + 1);
                feature = (subfeatures ? feature.GetNextSubFeature() : feature.GetNextFeature()) as IFeature;
            }
        }
    }
}
