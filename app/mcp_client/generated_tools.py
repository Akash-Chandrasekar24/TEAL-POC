"""Generated from the SolidWorks MCP tool schemas. Do not edit by hand."""
from __future__ import annotations

from typing import Any

# name -> kind: read | lifecycle | model
TOOL_KIND: dict[str, str] = {
    "activate_document": "lifecycle",
    "add_dimension": "model",
    "add_drawing_dimension": "model",
    "add_drawing_view": "model",
    "add_edge_feature": "model",
    "add_flat_pattern_view": "model",
    "add_hole_callout": "model",
    "add_mate": "model",
    "add_reference_geometry": "model",
    "add_section_view": "model",
    "add_sketch_constraint": "model",
    "add_sketch_entities": "model",
    "add_sketch_entity": "model",
    "analyze_assembly": "read",
    "analyze_drawing": "read",
    "analyze_model": "read",
    "analyze_slddrw_test": "read",
    "auto_center_marks": "model",
    "auto_dimension_drawing": "model",
    "batch_export": "lifecycle",
    "close_document": "lifecycle",
    "compare_assemblies": "read",
    "compare_parts": "read",
    "create_drawing": "model",
    "create_pattern": "model",
    "create_rib": "model",
    "create_sketch": "model",
    "edit_feature": "model",
    "edit_sketch": "model",
    "ensure_ready": "lifecycle",
    "export_document": "lifecycle",
    "extrude_feature": "model",
    "get_recipe": "read",
    "get_selection": "read",
    "insert_component": "model",
    "modify_dimension": "model",
    "open_document": "lifecycle",
    "open_new_assembly": "lifecycle",
    "open_new_part": "lifecycle",
    "rebuild_from_ir": "model",
    "save_analysis": "lifecycle",
    "save_body_as_part": "lifecycle",
    "save_document": "lifecycle",
    "set_part_material": "model",
    "sheet_metal_feature": "model",
    "submit_feature_graph": "model",
    "verify_state": "read",
}


class ToolsMixin:
    """One typed method per adapter tool. Needs `call(tool, args, timeout)` from the client."""

    async def activate_document(self, title: str, *, timeout: float | None = None):
        """[lifecycle] Switch the ACTIVE SolidWorks document to an already-open one by its title (e.g. 'gear' or 'gear.SLDPRT'). Use this to read/compare another open part — e.g. acti"""
        args: dict[str, Any] = {"title": title}
        return await self.call("activate_document", {k: v for k, v in args.items() if v is not None}, timeout)

    async def add_dimension(self, px: float, py: float, value: float, *, label_offset_x: float | None = None, label_offset_y: float | None = None, timeout: float | None = None):
        """[model] Apply a smart dimension to the sketch segment nearest to point (px, py). px/py should be on or very near the target segment — e.g. the midpoint of a line. label"""
        args: dict[str, Any] = {"px": px, "py": py, "value": value, "label_offset_x": label_offset_x, "label_offset_y": label_offset_y}
        return await self.call("add_dimension", {k: v for k, v in args.items() if v is not None}, timeout)

    async def add_drawing_dimension(self, px: float, py: float, value: float, *, label_offset_x: float | None = None, label_offset_y: float | None = None, timeout: float | None = None):
        """[model] Add a smart dimension to a drawing view segment nearest to point (px, py). Works the same way as add_dimension but operates in a drawing document context. px/py"""
        args: dict[str, Any] = {"px": px, "py": py, "value": value, "label_offset_x": label_offset_x, "label_offset_y": label_offset_y}
        return await self.call("add_drawing_dimension", {k: v for k, v in args.items() if v is not None}, timeout)

    async def add_drawing_view(self, view_type: str, *, pos_x: float | None = None, pos_y: float | None = None, scale: float | None = None, model_path: str | None = None, display_mode: str | None = None, timeout: float | None = None):
        """[model] Add a model view to the active drawing sheet. view_type: 'front', 'top', 'right', 'isometric', 'back', 'bottom', 'left'. pos_x/pos_y: position on the drawing sh"""
        args: dict[str, Any] = {"view_type": view_type, "pos_x": pos_x, "pos_y": pos_y, "scale": scale, "model_path": model_path, "display_mode": display_mode}
        return await self.call("add_drawing_view", {k: v for k, v in args.items() if v is not None}, timeout)

    async def add_edge_feature(self, feature_type: str, radius_or_distance: float, *, edge_indices: str | None = None, ex: float | None = None, ey: float | None = None, ez: float | None = None, edges_json: str | None = None, chamfer_type: str | None = None, angle: float | None = None, distance2: float | None = None, chamfer_flip: bool | None = None, timeout: float | None = None):
        """[model] Apply a 3D edge modifier to solid body edges (post-extrusion). Distinct from sketch fillet/chamfer. feature_type='fillet': rounds selected edge(s) with given ra"""
        args: dict[str, Any] = {"feature_type": feature_type, "radius_or_distance": radius_or_distance, "edge_indices": edge_indices, "ex": ex, "ey": ey, "ez": ez, "edges_json": edges_json, "chamfer_type": chamfer_type, "angle": angle, "distance2": distance2, "chamfer_flip": chamfer_flip}
        return await self.call("add_edge_feature", {k: v for k, v in args.items() if v is not None}, timeout)

    async def add_flat_pattern_view(self, *, pos_x: float | None = None, pos_y: float | None = None, scale: float | None = None, model_path: str | None = None, config_name: str | None = None, hide_bend_lines: bool | None = None, flip_view: bool | None = None, timeout: float | None = None):
        """[model] Add a FLAT PATTERN view of a SHEET-METAL part to the active drawing — the unfolded blank with bend lines/notes. Sheet metal is dimensioned on its FLAT PATTERN ("""
        args: dict[str, Any] = {"pos_x": pos_x, "pos_y": pos_y, "scale": scale, "model_path": model_path, "config_name": config_name, "hide_bend_lines": hide_bend_lines, "flip_view": flip_view}
        return await self.call("add_flat_pattern_view", {k: v for k, v in args.items() if v is not None}, timeout)

    async def add_hole_callout(self, px: float, py: float, *, timeout: float | None = None):
        """[model] Insert a hole callout (e.g. '4× Ø8 THRU') on the hole edge nearest the sheet point (px, py) in the active drawing. Coordinate-based selection — same fragile poi"""
        args: dict[str, Any] = {"px": px, "py": py}
        return await self.call("add_hole_callout", {k: v for k, v in args.items() if v is not None}, timeout)

    async def add_mate(self, mate_type: str, a_component: str, a_index: int, b_component: str, b_index: int, *, a_kind: str | None = None, b_kind: str | None = None, alignment: str | None = None, flip: bool | None = None, value: float | None = None, timeout: float | None = None):
        """[model] Add a mate between two component entities in the ACTIVE assembly. Entity selection is INDEX-based (robust — coordinate picks miss real geometry): each side is ("""
        args: dict[str, Any] = {"mate_type": mate_type, "a_component": a_component, "a_index": a_index, "b_component": b_component, "b_index": b_index, "a_kind": a_kind, "b_kind": b_kind, "alignment": alignment, "flip": flip, "value": value}
        return await self.call("add_mate", {k: v for k, v in args.items() if v is not None}, timeout)

    async def add_reference_geometry(self, type: str, *, ref_plane_name: str | None = None, offset: float | None = None, entity1_name: str | None = None, entity1_type: str | None = None, entity2_name: str | None = None, entity2_type: str | None = None, px: float | None = None, py: float | None = None, pz: float | None = None, timeout: float | None = None):
        """[model] Create reference geometry (plane, axis, or point) in the active part. type='plane': offset plane from ref_plane_name by offset (meters). E.g. ref_plane_name='Fr"""
        args: dict[str, Any] = {"type": type, "ref_plane_name": ref_plane_name, "offset": offset, "entity1_name": entity1_name, "entity1_type": entity1_type, "entity2_name": entity2_name, "entity2_type": entity2_type, "px": px, "py": py, "pz": pz}
        return await self.call("add_reference_geometry", {k: v for k, v in args.items() if v is not None}, timeout)

    async def add_section_view(self, px: float, py: float, *, edge_x: Any | None = None, edge_y: Any | None = None, x1: Any | None = None, y1: Any | None = None, x2: Any | None = None, y2: Any | None = None, label: str | None = None, flip: bool | None = None, scale: float | None = None, timeout: float | None = None):
        """[model] Create a SECTION VIEW that cuts the model so internal/blind features become visible, dimensionable edges. Use this when a feature's size can't be shown in a pro"""
        args: dict[str, Any] = {"px": px, "py": py, "edge_x": edge_x, "edge_y": edge_y, "x1": x1, "y1": y1, "x2": x2, "y2": y2, "label": label, "flip": flip, "scale": scale}
        return await self.call("add_section_view", {k: v for k, v in args.items() if v is not None}, timeout)

    async def add_sketch_constraint(self, constraint_type: str, px1: float, py1: float, *, px2: float | None = None, py2: float | None = None, entity_type1: str | None = None, entity_type2: str | None = None, timeout: float | None = None):
        """[model] Apply a geometric constraint to sketch entities in the active sketch. constraint_type: 'horizontal', 'vertical' — single entity (px1, py1 only). constraint_type"""
        args: dict[str, Any] = {"constraint_type": constraint_type, "px1": px1, "py1": py1, "px2": px2, "py2": py2, "entity_type1": entity_type1, "entity_type2": entity_type2}
        return await self.call("add_sketch_constraint", {k: v for k, v in args.items() if v is not None}, timeout)

    async def add_sketch_entities(self, segments: str, *, timeout: float | None = None):
        """[model] Add MANY sketch entities to the active sketch in ONE call (the batch form of add_sketch_entity — prefer this whenever a profile has more than ~3 segments). segm"""
        args: dict[str, Any] = {"segments": segments}
        return await self.call("add_sketch_entities", {k: v for k, v in args.items() if v is not None}, timeout)

    async def add_sketch_entity(self, entity_type: str, *, x1: float | None = None, y1: float | None = None, x2: float | None = None, y2: float | None = None, xm: float | None = None, ym: float | None = None, cx: float | None = None, cy: float | None = None, radius: float | None = None, vx: float | None = None, vy: float | None = None, distance: float | None = None, direction: float | None = None, points: str | None = None, construction: bool | None = None, timeout: float | None = None):
        """[model] Add a sketch entity to the active sketch. entity_type='rectangle': uses x1,y1 (first corner) and x2,y2 (opposite corner). entity_type='circle': uses cx,cy (cent"""
        args: dict[str, Any] = {"entity_type": entity_type, "x1": x1, "y1": y1, "x2": x2, "y2": y2, "xm": xm, "ym": ym, "cx": cx, "cy": cy, "radius": radius, "vx": vx, "vy": vy, "distance": distance, "direction": direction, "points": points, "construction": construction}
        return await self.call("add_sketch_entity", {k: v for k, v in args.items() if v is not None}, timeout)

    async def analyze_assembly(self, analysis_type: str, *, component: str | None = None, timeout: float | None = None):
        """[read] Analyze the active ASSEMBLY document (read-only, does NOT change state). analysis_type='components': top-level component occurrences in FEATURE-TREE order — per"""
        args: dict[str, Any] = {"analysis_type": analysis_type, "component": component}
        return await self.call("analyze_assembly", {k: v for k, v in args.items() if v is not None}, timeout)

    async def analyze_drawing(self, file_path: str, *, save_analysis: bool | None = None, mode: str | None = None, timeout: float | None = None):
        """[read] Read a 2D technical drawing — **.DXF or .DWG** — and either BUILD the part from it or hand you the evidence to build it yourself. This is the drawing→part front"""
        args: dict[str, Any] = {"file_path": file_path, "save_analysis": save_analysis, "mode": mode}
        return await self.call("analyze_drawing", {k: v for k, v in args.items() if v is not None}, timeout)

    async def analyze_model(self, analysis_type: str, *, name: str | None = None, from_feature: str | None = None, to_feature: str | None = None, near: str | None = None, k: int | None = None, axis: str | None = None, timeout: float | None = None):
        """[read] Analyze the active SolidWorks part document (read-only, does NOT change state). analysis_type='mass_properties': returns volume, surface_area, center of gravity"""
        args: dict[str, Any] = {"analysis_type": analysis_type, "name": name, "from_feature": from_feature, "to_feature": to_feature, "near": near, "k": k, "axis": axis}
        return await self.call("analyze_model", {k: v for k, v in args.items() if v is not None}, timeout)

    async def analyze_slddrw_test(self, *, include_geometry: bool | None = None, include_relations: bool | None = None, timeout: float | None = None):
        """[read] TEST/REFERENCE TOOL — reads a NATIVE SolidWorks drawing (.SLDDRW) through COM. ⚠ NOT the shipping drawing→part path. A .SLDDRW is SLDPRT-linked (its views go bl"""
        args: dict[str, Any] = {"include_geometry": include_geometry, "include_relations": include_relations}
        return await self.call("analyze_slddrw_test", {k: v for k, v in args.items() if v is not None}, timeout)

    async def auto_center_marks(self, *, include_slots: bool | None = None, extended_lines: bool | None = None, timeout: float | None = None):
        """[model] Automatically place center marks (with extended centerlines) on every hole/slot in every model view of the active drawing (SolidWorks 'Auto Insert > Center Mark"""
        args: dict[str, Any] = {"include_slots": include_slots, "extended_lines": extended_lines}
        return await self.call("auto_center_marks", {k: v for k, v in args.items() if v is not None}, timeout)

    async def auto_dimension_drawing(self, *, all_views: bool | None = None, include_unmarked: bool | None = None, eliminate_duplicates: bool | None = None, timeout: float | None = None):
        """[model] Transfer the MODEL's driving dimensions into the active drawing's views (SolidWorks 'Insert Model Items > Dimensions'). PREFER this over add_drawing_dimension's"""
        args: dict[str, Any] = {"all_views": all_views, "include_unmarked": include_unmarked, "eliminate_duplicates": eliminate_duplicates}
        return await self.call("auto_dimension_drawing", {k: v for k, v in args.items() if v is not None}, timeout)

    async def batch_export(self, file_path_base: str, formats_json: str, *, timeout: float | None = None):
        """[lifecycle] Export the active document to multiple formats in one call. file_path_base: output path without extension (e.g. 'C:/output/mypart'). formats_json: JSON array of"""
        args: dict[str, Any] = {"file_path_base": file_path_base, "formats_json": formats_json}
        return await self.call("batch_export", {k: v for k, v in args.items() if v is not None}, timeout)

    async def close_document(self, *, save: bool | None = None, close_all: bool | None = None, timeout: float | None = None):
        """[lifecycle] Close the active SolidWorks document. Set save=True to save before closing. close_all=True closes ALL open documents (incl. invisibly-loaded component docs that"""
        args: dict[str, Any] = {"save": save, "close_all": close_all}
        return await self.call("close_document", {k: v for k, v in args.items() if v is not None}, timeout)

    async def compare_assemblies(self, doc_a: str, doc_b: str, *, timeout: float | None = None):
        """[read] Objectively diff two ASSEMBLY documents against the RATIFIED verified criteria (ADR-047): component set EXACT (source file + config + instance counts) AND every"""
        args: dict[str, Any] = {"doc_a": doc_a, "doc_b": doc_b}
        return await self.call("compare_assemblies", {k: v for k, v in args.items() if v is not None}, timeout)

    async def compare_parts(self, doc_a: str, doc_b: str, *, detail: str | None = None, timeout: float | None = None):
        """[read] Objectively diff two part documents — the round-trip verifier behind the artifact's `verified` label (and a general-purpose 'are these the same part?' check). d"""
        args: dict[str, Any] = {"doc_a": doc_a, "doc_b": doc_b, "detail": detail}
        return await self.call("compare_parts", {k: v for k, v in args.items() if v is not None}, timeout)

    async def create_drawing(self, *, model_path: str | None = None, timeout: float | None = None):
        """[model] Open a new SolidWorks drawing document (A3 sheet, 1:1 scale). model_path: optional path to the part/assembly to reference. If omitted, an empty drawing is creat"""
        args: dict[str, Any] = {"model_path": model_path}
        return await self.call("create_drawing", {k: v for k, v in args.items() if v is not None}, timeout)

    async def create_pattern(self, pattern_type: str, *, feature_name: str | None = None, spacing: float | None = None, count: int | None = None, direction: str | None = None, count2: int | None = None, spacing2: float | None = None, flip: bool | None = None, axis_name: str | None = None, angle: float | None = None, equal_spacing: bool | None = None, features_json: str | None = None, plane: str | None = None, geometry_pattern: bool | None = None, timeout: float | None = None):
        """[model] Create a linear, circular, or mirror feature pattern. pattern_type='linear': repeats feature_name along direction ('X', 'Y', or 'Z') with spacing (meters) and c"""
        args: dict[str, Any] = {"pattern_type": pattern_type, "feature_name": feature_name, "spacing": spacing, "count": count, "direction": direction, "count2": count2, "spacing2": spacing2, "flip": flip, "axis_name": axis_name, "angle": angle, "equal_spacing": equal_spacing, "features_json": features_json, "plane": plane, "geometry_pattern": geometry_pattern}
        return await self.call("create_pattern", {k: v for k, v in args.items() if v is not None}, timeout)

    async def create_rib(self, thickness: float, *, two_sided: bool | None = None, reverse_thickness_dir: bool | None = None, reverse_material_dir: bool | None = None, is_norm_to_sketch: bool | None = None, timeout: float | None = None):
        """[model] Create a RIB from the ACTIVE sketch (consumes it, like extrude_feature). The rib profile is OPEN geometry — typically a single line (e.g. the diagonal between a"""
        args: dict[str, Any] = {"thickness": thickness, "two_sided": two_sided, "reverse_thickness_dir": reverse_thickness_dir, "reverse_material_dir": reverse_material_dir, "is_norm_to_sketch": is_norm_to_sketch}
        return await self.call("create_rib", {k: v for k, v in args.items() if v is not None}, timeout)

    async def create_sketch(self, *, plane: str | None = None, on_face: bool | None = None, face_index: int | None = None, face_x: float | None = None, face_y: float | None = None, face_z: float | None = None, timeout: float | None = None):
        """[model] Create a new sketch on a plane OR on an existing model face. Default (on_face=False): provide plane (e.g. 'Front Plane', 'Top Plane', or a named ref plane like """
        args: dict[str, Any] = {"plane": plane, "on_face": on_face, "face_index": face_index, "face_x": face_x, "face_y": face_y, "face_z": face_z}
        return await self.call("create_sketch", {k: v for k, v in args.items() if v is not None}, timeout)

    async def edit_feature(self, feature_name: str, action: str, *, new_name: str | None = None, timeout: float | None = None):
        """[model] Structurally edit an existing feature by name (suppress / unsuppress / delete / rename). feature_name: exact feature-tree name, e.g. 'Boss-Extrude1', 'Cut-Extru"""
        args: dict[str, Any] = {"feature_name": feature_name, "action": action, "new_name": new_name}
        return await self.call("edit_feature", {k: v for k, v in args.items() if v is not None}, timeout)

    async def edit_sketch(self, sketch_name: str, *, timeout: float | None = None):
        """[model] Reopen an existing sketch for editing. Counterpart to create_sketch. sketch_name: exact name of the sketch feature in the feature tree (e.g. 'Sketch1'). Returns"""
        args: dict[str, Any] = {"sketch_name": sketch_name}
        return await self.call("edit_sketch", {k: v for k, v in args.items() if v is not None}, timeout)

    async def ensure_ready(self, *, timeout: float | None = None):
        """[lifecycle] Bring the SolidWorks environment up and confirm it is ready to use. Starts the execution server if it isn't running, and launches SolidWorks if it is closed (ju"""
        args: dict[str, Any] = {}
        return await self.call("ensure_ready", {k: v for k, v in args.items() if v is not None}, timeout)

    async def export_document(self, format: str, file_path: str, *, timeout: float | None = None):
        """[lifecycle] Export the active SolidWorks document to a file. The document remains open after export. format: 'STEP', 'IGES', 'STL', 'PDF', 'DWG', 'DXF'. file_path: full out"""
        args: dict[str, Any] = {"format": format, "file_path": file_path}
        return await self.call("export_document", {k: v for k, v in args.items() if v is not None}, timeout)

    async def extrude_feature(self, *, depth: float | None = None, feature_type: str | None = None, angle: float | None = None, axis_x1: float | None = None, axis_y1: float | None = None, axis_x2: float | None = None, axis_y2: float | None = None, path_sketch: str | None = None, profiles: str | None = None, reverse: bool | None = None, through: bool | None = None, up_to_face_index: int | None = None, mid_plane: bool | None = None, timeout: float | None = None):
        """[model] Extrude/feature the active sketch profile. feature_type='boss' (default): solid extrusion, requires depth. feature_type='cut': material removal, requires depth """
        args: dict[str, Any] = {"depth": depth, "feature_type": feature_type, "angle": angle, "axis_x1": axis_x1, "axis_y1": axis_y1, "axis_x2": axis_x2, "axis_y2": axis_y2, "path_sketch": path_sketch, "profiles": profiles, "reverse": reverse, "through": through, "up_to_face_index": up_to_face_index, "mid_plane": mid_plane}
        return await self.call("extrude_feature", {k: v for k, v in args.items() if v is not None}, timeout)

    async def get_recipe(self, *, section: str | None = None, profile: str | None = None, timeout: float | None = None):
        """[read] The IR-generation recipe — REQUIRED READING before writing any Feature Graph IR (an artifact's `ir.graph`), reconstructing a part from a drawing, or producing a"""
        args: dict[str, Any] = {"section": section, "profile": profile}
        return await self.call("get_recipe", {k: v for k, v in args.items() if v is not None}, timeout)

    async def get_selection(self, *, timeout: float | None = None):
        """[read] Report what the USER currently has selected in the SolidWorks GUI — the inverse of index-based selection. When the user clicks geometry in the SolidWorks window"""
        args: dict[str, Any] = {}
        return await self.call("get_selection", {k: v for k, v in args.items() if v is not None}, timeout)

    async def insert_component(self, file_path: str, *, x: float | None = None, y: float | None = None, z: float | None = None, config: str | None = None, fixed: bool | None = None, transform_json: str | None = None, timeout: float | None = None):
        """[model] Insert a part file as a component of the ACTIVE assembly (open_new_assembly first). file_path: the .SLDPRT to insert (meters for all coordinates). x/y/z: insert"""
        args: dict[str, Any] = {"file_path": file_path, "x": x, "y": y, "z": z, "config": config, "fixed": fixed, "transform_json": transform_json}
        return await self.call("insert_component", {k: v for k, v in args.items() if v is not None}, timeout)

    async def modify_dimension(self, name: str, value: float, *, timeout: float | None = None):
        """[model] Change a named display dimension's value — the UNIVERSAL parametric edit (variant keystone). name: the dimension full-name exactly as analyze_model(features) re"""
        args: dict[str, Any] = {"name": name, "value": value}
        return await self.call("modify_dimension", {k: v for k, v in args.items() if v is not None}, timeout)

    async def open_document(self, file_path: str, *, as_assembly: bool | None = None, timeout: float | None = None):
        """[lifecycle] Open an EXISTING document from disk (counterpart to open_new_part, which makes a BLANK doc). file_path: full path to the file. - Native .sldprt / .sldasm / .sld"""
        args: dict[str, Any] = {"file_path": file_path, "as_assembly": as_assembly}
        return await self.call("open_document", {k: v for k, v in args.items() if v is not None}, timeout)

    async def open_new_assembly(self, *, template_path: str | None = None, timeout: float | None = None):
        """[lifecycle] Open a new blank SolidWorks ASSEMBLY document (the twin of open_new_part). Use before insert_component / add_mate. template_path: optional .asmdot template (def"""
        args: dict[str, Any] = {"template_path": template_path}
        return await self.call("open_new_assembly", {k: v for k, v in args.items() if v is not None}, timeout)

    async def open_new_part(self, *, template_path: str | None = None, timeout: float | None = None):
        """[lifecycle] Open a new SolidWorks part document."""
        args: dict[str, Any] = {"template_path": template_path}
        return await self.call("open_new_part", {k: v for k, v in args.items() if v is not None}, timeout)

    async def rebuild_from_ir(self, artifact_path: str, *, fresh_document: bool | None = None, timeout: float | None = None):
        """[model] Rebuild a part from its analysis artifact's Feature Graph IR — the verification half of the round-trip ('the LLM proposes, the round-trip decides', IR-ADR-006)."""
        args: dict[str, Any] = {"artifact_path": artifact_path, "fresh_document": fresh_document}
        return await self.call("rebuild_from_ir", {k: v for k, v in args.items() if v is not None}, timeout)

    async def save_analysis(self, file_path: str, *, timeout: float | None = None):
        """[lifecycle] Analyze a part FILE and persist its analysis ARTIFACT — the entry tool of the analysis pipeline. Opens the part (activates it if already open), runs the standar"""
        args: dict[str, Any] = {"file_path": file_path}
        return await self.call("save_analysis", {k: v for k, v in args.items() if v is not None}, timeout)

    async def save_body_as_part(self, body_index: int, file_path: str, *, timeout: float | None = None):
        """[lifecycle] Extract ONE solid body of the ACTIVE multibody part into its own .SLDPRT file. body_index: from analyze_model(analysis_type='bodies'). The body's geometry stays"""
        args: dict[str, Any] = {"body_index": body_index, "file_path": file_path}
        return await self.call("save_body_as_part", {k: v for k, v in args.items() if v is not None}, timeout)

    async def save_document(self, *, file_path: str | None = None, timeout: float | None = None):
        """[lifecycle] Save the active SolidWorks document to disk. file_path: full output path including the document extension (.sldprt for parts, .sldasm for assemblies, .slddrw fo"""
        args: dict[str, Any] = {"file_path": file_path}
        return await self.call("save_document", {k: v for k, v in args.items() if v is not None}, timeout)

    async def set_part_material(self, material_name: str, *, library: str | None = None, timeout: float | None = None):
        """[model] Assign a material to the active part document. Applied to all configurations. material_name: exact library name (e.g. '1060 Alloy', 'AISI 1020', 'ABS') — must m"""
        args: dict[str, Any] = {"material_name": material_name, "library": library}
        return await self.call("set_part_material", {k: v for k, v in args.items() if v is not None}, timeout)

    async def sheet_metal_feature(self, feature_type: str, *, thickness: float | None = None, bend_radius: float | None = None, k_factor: float | None = None, ex: float | None = None, ey: float | None = None, ez: float | None = None, flange_length: float | None = None, angle: float | None = None, use_default_radius: bool | None = None, flip: bool | None = None, bend_position: str | None = None, fixed_face_index: int | None = None, fixed_x: float | None = None, fixed_y: float | None = None, fixed_z: float | None = None, reverse_thickness: bool | None = None, symmetric_thickness: bool | None = None, clear_profile: bool | None = None, edge_index: int | None = None, timeout: float | None = None):
        """[model] Create sheet metal features on the active part. Lengths METERS, angles DEGREES. 'base_flange' — a sheet metal base from the ACTIVE sketch profile; exits sketch """
        args: dict[str, Any] = {"feature_type": feature_type, "thickness": thickness, "bend_radius": bend_radius, "k_factor": k_factor, "ex": ex, "ey": ey, "ez": ez, "flange_length": flange_length, "angle": angle, "use_default_radius": use_default_radius, "flip": flip, "bend_position": bend_position, "fixed_face_index": fixed_face_index, "fixed_x": fixed_x, "fixed_y": fixed_y, "fixed_z": fixed_z, "reverse_thickness": reverse_thickness, "symmetric_thickness": symmetric_thickness, "clear_profile": clear_profile, "edge_index": edge_index}
        return await self.call("sheet_metal_feature", {k: v for k, v in args.items() if v is not None}, timeout)

    async def submit_feature_graph(self, graph: str, *, fresh_document: bool | None = None, timeout: float | None = None):
        """[model] Build a part (or assembly) from a Feature Graph IR in ONE call — the deterministic compiler lowers each IR node to the right low-level tool sequence and resolve"""
        args: dict[str, Any] = {"graph": graph, "fresh_document": fresh_document}
        return await self.call("submit_feature_graph", {k: v for k, v in args.items() if v is not None}, timeout)

    async def verify_state(self, *, timeout: float | None = None):
        """[read] Read and return the current CAD state without modifying it."""
        args: dict[str, Any] = {}
        return await self.call("verify_state", {k: v for k, v in args.items() if v is not None}, timeout)
