"""Make the synthetic multi-page benchmark sets (eval/files/*.pdf) from single-page drawings: python eval/make_synthetic.py

A page is stored at 150 DPI, so rendering it back at 150 DPI (app.ingest PDF_DPI_BENCH) gives the same pixels as the crop:
the view points in benchmark.json are in those pixels.
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DPI = 150
SPLITS = {"m01_triple_clamp_2pages": ("inputs/image_11_triple_clamp.png", 1290), "m02_pipe_fitting_2pages": ("inputs/image_2.png", 840)}


def main() -> None:
    out_dir = ROOT / "eval" / "files"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, (src, cut) in SPLITS.items():
        im = Image.open(ROOT / src).convert("RGB")
        pages = [im.crop((0, 0, cut, im.height)), im.crop((cut, 0, im.width, im.height))]
        pages[0].save(out_dir / f"{name}.pdf", save_all=True, append_images=pages[1:], resolution=DPI)
        print("wrote", out_dir / f"{name}.pdf", [p.size for p in pages])


if __name__ == "__main__":
    main()
