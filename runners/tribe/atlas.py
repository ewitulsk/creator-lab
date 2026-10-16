"""Cortical regions for TRIBE outputs on fsaverage5 (2 x 10,242 vertices).

Areas are the 180 HCP-MMP1 (Glasser 2016) areas per hemisphere, the parcellation
tribev2 itself uses, read straight from the FreeSurfer annotation files (fetched once by
mne). Networks are coarse groupings of those areas, chosen by hand and approximate:

- visual: early, dorsal and ventral visual areas and MT+
- auditory: early auditory and auditory belt/parabelt
- language: inferior frontal (44/45/47l), 55b, STS/STG, TPOJ1, posterior temporal
- default_mode: posterior/anterior cingulate and medial prefrontal, angular gyrus (PGi/PGs)
- attention: dorsal attention (FEF, PEF, 6a, superior parietal / intraparietal)
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

N_HEMI = 10242  # fsaverage5 vertices per hemisphere (the first 10,242 of fsaverage)

NETWORKS: dict[str, tuple[str, ...]] = {
    "visual": (
        "V1",
        "V2",
        "V3",
        "V4",
        "V3A",
        "V3B",
        "V6",
        "V6A",
        "V7",
        "IPS1",
        "V3CD",
        "LO1",
        "LO2",
        "LO3",
        "V4t",
        "FST",
        "MT",
        "MST",
        "PH",
        "V8",
        "VVC",
        "PIT",
        "FFC",
        "VMV1",
        "VMV2",
        "VMV3",
    ),  # fmt: skip
    "auditory": ("A1", "LBelt", "MBelt", "PBelt", "RI", "A4", "A5", "TA2"),
    "language": (
        "44",
        "45",
        "47l",
        "IFSa",
        "IFSp",
        "IFJa",
        "55b",
        "SFL",
        "PSL",
        "STV",
        "STSdp",
        "STSda",
        "STSvp",
        "STSva",
        "STGa",
        "TPOJ1",
        "TE1a",
        "TE1p",
    ),  # fmt: skip
    "default_mode": (
        "31pv",
        "31pd",
        "31a",
        "d23ab",
        "v23ab",
        "7m",
        "POS1",
        "POS2",
        "RSC",
        "PCV",
        "PGi",
        "PGs",
        "s32",
        "a24",
        "p32",
        "10r",
        "10v",
        "10d",
        "9m",
    ),  # fmt: skip
    "attention": (
        "FEF",
        "PEF",
        "6a",
        "7AL",
        "7Am",
        "7PL",
        "7PC",
        "LIPv",
        "LIPd",
        "VIP",
        "IP0",
        "IP1",
        "IP2",
        "MIP",
        "AIP",
    ),  # fmt: skip
}


def _clean(name: bytes | str) -> str:
    name = name.decode() if isinstance(name, bytes) else name
    if name[:2] in ("L_", "R_"):
        name = name[2:]
    return name.removesuffix("_ROI")


@lru_cache
def load_atlas(cache_dir: str) -> dict:
    """{'areas': [180 names], 'vertex_area': int16 (20484,) index or -1, 'networks': {name: [area idx]}}"""
    import mne
    import nibabel.freesurfer as fs

    subjects = Path(cache_dir) / "subjects"
    subjects.mkdir(parents=True, exist_ok=True)
    label_dir = subjects / "fsaverage" / "label"
    if not (label_dir / "lh.HCPMMP1.annot").exists():
        mne.datasets.fetch_hcp_mmp_parcellation(subjects_dir=subjects, combine=False, accept=True, verbose=False)
    areas: list[str] = []
    index: dict[str, int] = {}
    vertex_area = np.full(2 * N_HEMI, -1, dtype=np.int16)
    for h, hemi in enumerate(("lh", "rh")):
        labels, _ctab, names = fs.read_annot(str(label_dir / f"{hemi}.HCPMMP1.annot"))
        names = [_clean(n) for n in names]
        for _i, name in enumerate(names):
            if name in ("???", "Unknown", "") or name.startswith("?"):
                continue
            if name not in index:
                index[name] = len(areas)
                areas.append(name)
        lab = labels[:N_HEMI]
        for raw, name in enumerate(names):
            if name in index:
                vertex_area[h * N_HEMI : (h + 1) * N_HEMI][lab == raw] = index[name]
    networks = {net: sorted(index[a] for a in members if a in index) for net, members in NETWORKS.items()}
    return {"areas": areas, "vertex_area": vertex_area, "networks": networks}


def region_table(preds: np.ndarray, atlas: dict, times: np.ndarray):
    """Network and area means over time (bilateral). preds: (T, 20484)."""
    import pandas as pd

    va = atlas["vertex_area"]
    data = preds.astype(np.float32)
    cols: dict[str, np.ndarray] = {"t": times.astype(np.float32)}
    area_means = {}
    for i, _name in enumerate(atlas["areas"]):
        mask = va == i
        if mask.any():
            area_means[i] = np.nanmean(data[:, mask], axis=1)
    for net, members in atlas["networks"].items():
        mask = np.isin(va, members)
        cols[f"net_{net}"] = np.nanmean(data[:, mask], axis=1) if mask.any() else np.full(len(times), np.nan)
    for i, series in area_means.items():
        cols[f"area_{atlas['areas'][i]}"] = series
    return pd.DataFrame(cols)


def export_atlas_json(cache_dir: str, out_path: Path) -> None:
    """For the brain player: area per vertex and the network membership."""
    atlas = load_atlas(cache_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "mesh": "fsaverage5",
                "parcellation": "HCP-MMP1 (Glasser 2016)",
                "areas": atlas["areas"],
                "vertex_area": atlas["vertex_area"].tolist(),
                "networks": {k: [atlas["areas"][i] for i in v] for k, v in atlas["networks"].items()},
            }
        ),
        encoding="utf-8",
    )
