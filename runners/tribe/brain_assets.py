"""Export the fsaverage5 inflated cortex (and the HCP atlas) for the brain player.

nilearn ships fsaverage5, so this needs no download (the atlas fetch does, once).
Writes DATA_ROOT/brain/:
    mesh.bin    float32 coords, uint32 faces and float32 sulcal depth per hemisphere
    mesh.json   where each array sits in mesh.bin
    atlas.json  HCP-MMP1 area per vertex and the network groupings
Vertex order matches TRIBE's predictions: left hemisphere (10,242) then right (10,242).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

log = logging.getLogger("runner.tribe")

FORMAT = "creator-lab-mesh-1"


def pack_mesh(hemis: dict[str, dict[str, np.ndarray]]) -> tuple[bytes, dict]:
    """hemis: {"left": {"coords": (n, 3), "faces": (m, 3), "sulc": (n,)}, "right": {...}}."""
    blobs: list[bytes] = []
    offset = 0
    meta = {
        "format": FORMAT,
        "mesh": "fsaverage5",
        "surface": "inflated",
        "vertex_order": "left then right",
        "hemis": [],
    }
    for name in ("left", "right"):
        h = hemis[name]
        coords = np.ascontiguousarray(h["coords"], dtype=np.float32)
        faces = np.ascontiguousarray(h["faces"], dtype=np.uint32)
        sulc = np.ascontiguousarray(h["sulc"], dtype=np.float32)
        if coords.ndim != 2 or coords.shape[1] != 3 or faces.ndim != 2 or faces.shape[1] != 3:
            raise ValueError(f"{name}: coords and faces must be (n, 3)")
        if sulc.shape[0] != coords.shape[0] or faces.max() >= coords.shape[0]:
            raise ValueError(f"{name}: sulc or faces don't match the vertex count")
        entry = {"name": name, "n_vertices": int(coords.shape[0]), "n_faces": int(faces.shape[0])}
        for key, arr in (("coords", coords), ("faces", faces), ("sulc", sulc)):
            data = arr.tobytes()
            entry[key] = {"offset": offset, "dtype": str(arr.dtype), "length": int(arr.size)}
            blobs.append(data)
            offset += len(data)
        entry["bbox"] = [coords.min(axis=0).round(3).tolist(), coords.max(axis=0).round(3).tolist()]
        meta["hemis"].append(entry)
    meta["bytes"] = offset
    return b"".join(blobs), meta


def _load(path) -> tuple[np.ndarray, np.ndarray]:
    from nilearn import surface

    mesh = surface.load_surf_mesh(path)
    coords = getattr(mesh, "coordinates", None)
    faces = getattr(mesh, "faces", None)
    if coords is None:  # older nilearn returns a (coords, faces) tuple
        coords, faces = mesh
    return np.asarray(coords), np.asarray(faces)


def export_brain_assets(out_dir: Path, atlas_cache: Path | None = None) -> Path:
    from nilearn import datasets, surface

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fs = datasets.fetch_surf_fsaverage("fsaverage5")
    hemis = {}
    for name in ("left", "right"):
        coords, faces = _load(fs[f"infl_{name}"])
        sulc = np.asarray(surface.load_surf_data(fs[f"sulc_{name}"]))
        hemis[name] = {"coords": coords, "faces": faces, "sulc": sulc}
    blob, meta = pack_mesh(hemis)
    (out_dir / "mesh.bin").write_bytes(blob)
    (out_dir / "mesh.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    if atlas_cache is not None:
        try:
            from .atlas import export_atlas_json

            export_atlas_json(str(atlas_cache), out_dir / "atlas.json")
        except Exception as err:  # the atlas needs a one-time download; the mesh doesn't
            log.warning("atlas export skipped: %s", err)
    log.info("brain assets written to %s", out_dir)
    return out_dir


def ensure_brain_assets(data_root: Path, cache_dir: Path) -> None:
    out = Path(data_root) / "brain"
    if (out / "mesh.json").exists() and (out / "mesh.bin").exists() and (out / "atlas.json").exists():
        return
    try:
        export_brain_assets(out, cache_dir / "atlas")
    except Exception as err:
        log.warning("could not export brain assets: %s", err)
