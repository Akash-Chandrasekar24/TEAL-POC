// Application-owned native drawing readback; never starts SolidWorks.
using System;
using System.Runtime.InteropServices;
using System.Web.Http;
using Newtonsoft.Json.Linq;
using SolidWorks.Interop.sldworks;
using SolidworksExecution.Infrastructure;

namespace DrawingToSolidworks
{
    public sealed class DrawingInspectionController : ApiController
    {
        [HttpGet, Route("api/drawing/inspect")]
        public IHttpActionResult Inspect()
        {
            try { return Ok(StaExecutor.Instance.Run(() => Read())); }
            catch (Exception ex) { return Content(System.Net.HttpStatusCode.Conflict, new { error = ex.Message }); }
        }

        private static JArray Numbers(object value, int size)
        {
            var array = value as double[];
            if (array == null || array.Length < size) throw new InvalidOperationException("Incomplete drawing geometry");
            var result = new JArray();
            for (int i = 0; i < size; i++)
            {
                if (Double.IsNaN(array[i]) || Double.IsInfinity(array[i])) throw new InvalidOperationException("Non-finite drawing geometry");
                result.Add(array[i]);
            }
            return result;
        }

        private static JObject Read()
        {
            var sw = (ISldWorks)Marshal.GetActiveObject("SldWorks.Application");
            var model = sw.IActiveDoc2 as IModelDoc2;
            var drawing = model as IDrawingDoc;
            if (drawing == null) throw new InvalidOperationException("Active document must be a drawing");
            var names = drawing.GetSheetNames() as object[];
            if (names == null || names.Length != 1) throw new InvalidOperationException("This workflow requires exactly one drawing sheet");
            var sheet = drawing.GetCurrentSheet() as ISheet;
            if (sheet == null) throw new InvalidOperationException("Missing active drawing sheet");
            double width = 0, height = 0;
            sheet.GetSize(ref width, ref height);
            var properties = sheet.GetProperties2() as double[];
            if (properties == null || properties.Length < 8) throw new InvalidOperationException("Incomplete sheet properties");
            if (width <= 0 || height <= 0 || Double.IsNaN(width) || Double.IsNaN(height))
                throw new InvalidOperationException("Invalid sheet size");
            var result = new JObject { ["version"] = 1, ["title"] = model.GetTitle(),
                ["path"] = model.GetPathName(), ["rebuild_ok"] = model.EditRebuild3(),
                ["sheet_count"] = names.Length, ["sheet_name"] = sheet.GetName(),
                ["first_angle"] = properties[4] != 0,
                ["sheet_size_m"] = new JArray(width, height) };
            var views = new JArray();
            var view = drawing.GetFirstView() as IView;
            if (view == null) throw new InvalidOperationException("Missing sheet container");
            view = view.GetNextView() as IView;
            int guard = 0;
            while (view != null)
            {
                if (++guard > 200) throw new InvalidOperationException("Incomplete view enumeration");
                var dimensions = new JArray();
                var display = view.GetFirstDisplayDimension5() as IDisplayDimension;
                int dimGuard = 0;
                while (display != null)
                {
                    if (++dimGuard > 20000) throw new InvalidOperationException("Incomplete dimension enumeration");
                    var dimension = display.GetDimension2(0) as IDimension;
                    if (dimension == null || Double.IsNaN(dimension.SystemValue) || Double.IsInfinity(dimension.SystemValue))
                        throw new InvalidOperationException("Unreadable native dimension");
                    dimensions.Add(new JObject { ["name"] = dimension.FullName, ["value_si"] = dimension.SystemValue });
                    display = display.GetNext5() as IDisplayDimension;
                }
                var references = new JArray();
                var referenced = view.ReferencedDocument as IModelDoc2;
                if (referenced == null) throw new InvalidOperationException("Unresolved referenced model");
                var assembly = referenced as IAssemblyDoc;
                if (assembly != null)
                {
                    var components = assembly.GetComponents(true) as object[];
                    if (components == null || components.Length == 0) throw new InvalidOperationException("Empty or unresolved referenced assembly");
                    foreach (var value in components)
                    {
                        var component = value as IComponent2;
                        if (component == null || component.IsSuppressed() || component.IGetChildrenCount() != 0 || component.GetModelDoc2() == null)
                            throw new InvalidOperationException("Referenced assembly must have resolved, unsuppressed flat leaf parts");
                        references.Add(new JObject { ["path"] = component.GetPathName(), ["configuration"] = component.ReferencedConfiguration });
                    }
                }
                views.Add(new JObject { ["name"] = view.Name, ["orientation"] = view.GetOrientationName(),
                    ["model_path"] = view.GetReferencedModelName(), ["configuration"] = view.ReferencedConfiguration,
                    ["scale"] = view.ScaleDecimal, ["position_m"] = Numbers(view.Position, 2),
                    ["outline_m"] = Numbers(view.GetOutline(), 4), ["dimensions"] = dimensions,
                    ["component_references"] = references });
                view = view.GetNextView() as IView;
            }
            result["views"] = views;
            result["view_count"] = views.Count;
            return result;
        }
    }
}
