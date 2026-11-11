"""Preview PNG rendered from a binary STL (SolidWorks export), in Python. The MCP has no image export."""
from __future__ import annotations

import struct
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402


def read_stl(path: Path) -> np.ndarray:
    """Triangles as an (n, 3, 3) float array, in the STL's own unit (SolidWorks writes mm)."""
    b = Path(path).read_bytes()
    n = struct.unpack("<I", b[80:84])[0]
    if len(b) != 84 + 50 * n:
        raise ValueError("not a binary STL")
    rec = np.frombuffer(b[84:], dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
    return rec["v"].astype(float)


def render_stl_png(stl: Path, png: Path, title: str = "", elev: float = 28, azim: float = -60) -> Path:
    tri = read_stl(stl)
    # camera direction (from the part towards the viewer), same convention as matplotlib's view_init
    e, a = np.radians(elev), np.radians(azim)
    to_cam = np.array([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)])
    nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-12
    keep = nrm @ to_cam > 0                              # back-face culling: only faces turned towards the camera
    tri, nrm = tri[keep], nrm[keep]
    order = np.argsort((tri.mean(axis=1) @ to_cam))      # painter's algorithm: far to near
    tri, nrm = tri[order], nrm[order]
    light = np.array([0.35, -0.55, 0.75])
    light /= np.linalg.norm(light)
    shade = 0.35 + 0.65 * np.clip(nrm @ light, 0, 1)
    colors = np.stack([0.55 * shade, 0.68 * shade, 0.85 * shade, np.ones_like(shade)], axis=1)

    allv = read_stl(stl).reshape(-1, 3)
    lo, hi = allv.min(axis=0), allv.max(axis=0)
    fig = plt.figure(figsize=(8, 6.5), dpi=130)
    ax = fig.add_subplot(111, projection="3d")
    ax.add_collection3d(Poly3DCollection(tri, facecolors=colors, edgecolors=(0, 0, 0, 0.10), linewidths=0.12))
    ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1]); ax.set_zlim(lo[2], hi[2])
    ax.set_box_aspect(hi - lo)
    ax.view_init(elev=elev, azim=azim)
    ax.set_xlabel("x (mm)"); ax.set_ylabel("y (mm)"); ax.set_zlabel("z (mm)")
    ax.set_title(f"{title}   {hi[0] - lo[0]:.0f} x {hi[1] - lo[1]:.0f} x {hi[2] - lo[2]:.0f} mm", fontsize=10)
    fig.tight_layout()
    png = Path(png)
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png)
    plt.close(fig)
    return png


def render_views_png(stl: Path, png: Path, title: str = "") -> Path:
    """The part as a drawing shows it: TOP (looking down -z: x right, y up), FRONT (x right, z up), SIDE (y right, z up) and a 3D view,
    shaded by how much each face turns to the viewer. What the SolidWorks agent compares with the drawing."""
    tri = read_stl(stl)
    nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-12
    # (title, right axis, up axis, depth axis, viewer side along the depth axis)
    views = (("TOP (x right, y up)", 0, 1, 2, 1.0), ("FRONT / elevation (x right, z up)", 0, 2, 1, -1.0), ("SIDE (y right, z up)", 1, 2, 0, 1.0))
    fig = plt.figure(figsize=(12, 9), dpi=110)
    for k, (name, a, b, d, side) in enumerate(views):
        ax = fig.add_subplot(2, 2, k + 1)
        img, ext = _zbuffer(tri, nrm, a, b, d, side)
        ax.imshow(img, origin="lower", extent=ext, cmap="Blues_r", vmin=0, vmax=1.25, interpolation="nearest")
        ax.set_title(name, fontsize=9); ax.grid(alpha=0.3)
    ax = fig.add_subplot(2, 2, 4, projection="3d")
    lo, hi = tri.reshape(-1, 3).min(axis=0), tri.reshape(-1, 3).max(axis=0)
    shade = 0.35 + 0.65 * np.clip(nrm @ (np.array([0.35, -0.55, 0.75]) / 1.0), 0, 1)
    ax.add_collection3d(Poly3DCollection(tri, facecolors=np.stack([0.55 * shade, 0.68 * shade, 0.85 * shade, np.ones_like(shade)], 1), edgecolors="none"))
    ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1]); ax.set_zlim(lo[2], hi[2]); ax.set_box_aspect(np.maximum(hi - lo, 1e-6)); ax.view_init(28, -60)
    fig.suptitle(f"{title}   {hi[0] - lo[0]:.1f} x {hi[1] - lo[1]:.1f} x {hi[2] - lo[2]:.1f} mm", fontsize=11)
    fig.tight_layout()
    png = Path(png); png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png); plt.close(fig)
    return png


def _zbuffer(tri: np.ndarray, nrm: np.ndarray, a: int, b: int, d: int, side: float, px: float = 0.4):
    """An orthographic picture along axis d (viewer on the `side` of it): per pixel the nearest face, shaded by how much it faces the viewer.
    Pixels with no part are white (1.25 on the colour scale). px = mm per pixel (coarser for big parts: at most ~900 pixels a side)."""
    pts = tri.reshape(-1, 3)
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    px = max(px, (hi[a] - lo[a]) / 900, (hi[b] - lo[b]) / 900, 1e-3)
    W, H = int((hi[a] - lo[a]) / px) + 2, int((hi[b] - lo[b]) / px) + 2
    depth = np.full((H, W), -np.inf); img = np.full((H, W), 1.25)
    U = (tri[:, :, a] - lo[a]) / px; V = (tri[:, :, b] - lo[b]) / px; Z = tri[:, :, d] * side
    shade = np.abs(nrm[:, d])
    for t in np.nonzero(np.abs(nrm[:, d]) > 1e-6)[0]:
        u, v, z = U[t], V[t], Z[t]
        x0, x1 = int(max(u.min(), 0)), int(min(np.ceil(u.max()), W - 1))
        y0, y1 = int(max(v.min(), 0)), int(min(np.ceil(v.max()), H - 1))
        if x1 < x0 or y1 < y0:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        den = (v[1] - v[2]) * (u[0] - u[2]) + (u[2] - u[1]) * (v[0] - v[2])
        if abs(den) < 1e-12:
            continue
        w0 = ((v[1] - v[2]) * (xs - u[2]) + (u[2] - u[1]) * (ys - v[2])) / den
        w1 = ((v[2] - v[0]) * (xs - u[2]) + (u[0] - u[2]) * (ys - v[2])) / den
        w2 = 1 - w0 - w1
        inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
        zz = w0 * z[0] + w1 * z[1] + w2 * z[2]
        sub = depth[y0:y1 + 1, x0:x1 + 1]
        win = inside & (zz > sub)
        sub[win] = zz[win]
        img[y0:y1 + 1, x0:x1 + 1][win] = 0.25 + 0.65 * (1 - shade[t])
    return img, (lo[a], lo[a] + W * px, lo[b], lo[b] + H * px)
