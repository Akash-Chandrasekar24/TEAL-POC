"""Content-addressed component files. Browser input never selects arbitrary paths."""
import hashlib
from pathlib import Path


MAX_MODEL_BYTES = 40 * 1024 * 1024


def import_model(name: str, data: bytes, folder: Path) -> dict:
    name = Path(name.replace("\\", "/")).name
    if Path(name).suffix.lower() != ".sldprt":
        raise ValueError("Supply a native SLDPRT part; STEP conversion and nested SLDASM are not supported here")
    if not data or len(data) > MAX_MODEL_BYTES:
        raise ValueError("Component models must be between 1 byte and 40 MB")
    # Native SolidWorks files use the OLE compound-document container. This is
    # format screening only; geometry/configuration checks require SolidWorks.
    if not data.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
        raise ValueError("The file does not have a native SolidWorks compound-document header")
    sha = hashlib.sha256(data).hexdigest()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha}.SLDPRT"
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("Stored component hash collision or corruption")
    else:
        # Exclusive creation; simultaneous identical uploads are harmless.
        try:
            with path.open("xb") as output:
                output.write(data)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError("Concurrent component upload did not complete; retry") from None
    return {"sha256": sha, "source_name": name, "bytes": len(data)}
