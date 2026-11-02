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
