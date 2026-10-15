"""tribe_engagement@1: engagement curves from TRIBE's predicted cortical response.

For each region: z-score every vertex over time, average the region's vertices, then
regress out the whole-cortex average signal (global signal regression). Curves per second:

    stimulus_drive     whole-cortex response magnitude: RMS of the z-scored vertices
                       (an inter-subject-correlation analog: how strongly the stimulus drives cortex)
    engagement_index   mean(dorsal attention, ventral attention/salience) minus default mode (draft)
    anterior_insula    HCP-MMP1 AVI + AAIC + MI (expected to predict drop-off: Tong et al. 2020)
    nacc               unavailable: there is no released subcortical TRIBE v2 checkpoint
    yeo7.<network>     each Yeo-7 network
    hcp.<area>         each HCP-MMP1 area (in engagement_regions.npz)

Atlases (vertex order: left hemisphere 10,242 then right 10,242):
    Yeo 2011 7 networks on fsaverage5 (FreeSurfer annotation, fetched once), or, if it
    can't be fetched, an approximation from HCP-MMP1 areas (recorded in the output).
    HCP-MMP1 from DATA_ROOT/brain/atlas.json (exported by the TRIBE runner).
"""

from __future__ import annotations

import json
import logging
import urllib.request
from pathlib import Path

import numpy as np

log = logging.getLogger("runner.engagement")

N_HEMI = 10242
YEO_NAMES = ["visual", "somatomotor", "dorsal_attention", "ventral_attention", "limbic", "frontoparietal", "default"]
YEO_URLS = {
    "lh": "https://raw.githubusercontent.com/satra/ds000114-freesurfer/master/fsaverage5/label/lh.yeo2011_7networks_n1000.annot",
    "rh": "https://raw.githubusercontent.com/satra/ds000114-freesurfer/master/fsaverage5/label/rh.yeo2011_7networks_n1000.annot",
}
# Fallback: Yeo-7-like groups of HCP-MMP1 areas (approximate).
YEO_FROM_HCP = {
    "visual": [
        "V1",
        "V2",
        "V3",
        "V4",
        "V3A",
        "V3B",
        "V6",
        "V7",
        "V8",
        "LO1",
        "LO2",
        "LO3",
        "PIT",
        "FFC",
        "VMV1",
        "VMV2",
        "VMV3",
        "VVC",
        "MT",
        "MST",
        "V4t",
        "FST",
    ],  # noqa: E501
    "somatomotor": [
        "4",
        "3a",
        "3b",
        "1",
        "2",
        "6d",
        "6mp",
        "6v",
        "OP4",
        "OP1",
        "OP2-3",
        "A1",
        "LBelt",
        "MBelt",
        "PBelt",
        "RI",
        "5m",
        "5L",
        "24dd",
        "24dv",
    ],  # noqa: E501
    "dorsal_attention": [
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
        "MIP",
        "AIP",
        "PH",
        "TPOJ3",
    ],  # noqa: E501
    "ventral_attention": [
        "AVI",
        "AAIC",
        "MI",
        "FOP1",
        "FOP3",
        "FOP4",
        "FOP5",
        "PoI1",
        "PoI2",
        "a24pr",
        "p32pr",
        "33pr",
        "SCEF",
        "46",
        "PSL",
        "SFL",
    ],  # noqa: E501
    "limbic": ["TGd", "TGv", "TE2a", "OFC", "pOFC", "13l", "11l", "10pp", "EC", "PeEc", "PHA1", "PHA2"],
    "frontoparietal": [
        "8C",
        "8Av",
        "IFJp",
        "IFSp",
        "p9-46v",
        "a9-46v",
        "9-46d",
        "a47r",
        "p47r",
        "PFm",
        "IP2",
        "AIP",
        "LIPd",
    ],  # noqa: E501
    "default": [
        "31pv",
        "31pd",
        "31a",
        "d23ab",
        "v23ab",
        "7m",
        "POS1",
        "PCV",
        "RSC",
        "PGi",
        "PGs",
        "TPOJ2",
        "s32",
        "a24",
        "p32",
        "10r",
        "10v",
        "10d",
        "9m",
        "STSda",
        "TE1a",
    ],  # noqa: E501
}
ANTERIOR_INSULA = ["AVI", "AAIC", "MI"]


def zscore_time(x: np.ndarray) -> np.ndarray:
    mean = np.nanmean(x, axis=0, keepdims=True)
    sd = np.nanstd(x, axis=0, keepdims=True)
    sd[sd == 0] = 1.0
    return (x - mean) / sd


def regress_out(y: np.ndarray, g: np.ndarray) -> np.ndarray:
    ok = np.isfinite(y) & np.isfinite(g)
    if ok.sum() < 3:
        return y
    gc = g[ok] - g[ok].mean()
    denom = float((gc**2).sum())
    beta = float(((y[ok] - y[ok].mean()) * gc).sum() / denom) if denom else 0.0
    return y - beta * (g - np.nanmean(g))


def region_curve(z: np.ndarray, mask: np.ndarray, g: np.ndarray) -> np.ndarray | None:
    if not mask.any():
        return None
    with np.errstate(invalid="ignore"):
        return regress_out(np.nanmean(z[:, mask], axis=1), g)


def load_yeo(cache_dir: Path | None) -> tuple[np.ndarray | None, str]:
    """(labels 0..7 per vertex, 0 = medial wall/unknown) or (None, reason)."""
    try:
        import nibabel.freesurfer as fs
    except ImportError:
        return None, "nibabel is not installed"
    if cache_dir is None:
        return None, "no cache folder"
    labels = []
    try:
        for hemi in ("lh", "rh"):
            path = Path(cache_dir) / f"{hemi}.Yeo2011_7Networks_N1000.fsaverage5.annot"
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                urllib.request.urlretrieve(YEO_URLS[hemi], path)
            lab, _ctab, names = fs.read_annot(str(path))
            lab = np.asarray(lab)[:N_HEMI]
            # Map annotation indices to network numbers via the names (7Networks_1 .. 7Networks_7).
            mapping = {}
            for i, n in enumerate(names):
                n = n.decode() if isinstance(n, bytes) else str(n)
                mapping[i] = int(n.rsplit("_", 1)[-1]) if n.lower().startswith("7networks_") else 0
            labels.append(np.vectorize(lambda v, m=mapping: m.get(int(v), 0))(lab))
    except Exception as err:
        return None, f"Yeo-7 annotation unavailable ({type(err).__name__}: {err})"
    return np.concatenate(labels).astype(np.int8), "Yeo 2011 7 networks (FreeSurfer fsaverage5 annotation)"


def hcp_masks(atlas: dict, names: list[str]) -> np.ndarray:
    areas = atlas["areas"]
    va = np.asarray(atlas["vertex_area"])
    idx = [areas.index(n) for n in names if n in areas]
    return np.isin(va, idx)


def compute(preds: np.ndarray, atlas: dict | None, yeo: np.ndarray | None) -> dict:
    x = preds.astype(np.float32)
    z = zscore_time(x)
    with np.errstate(invalid="ignore"):
        g = np.nanmean(z, axis=1)
        drive = np.sqrt(np.nanmean(z**2, axis=1))
    networks: dict[str, np.ndarray] = {}
    if yeo is not None:
        for i, name in enumerate(YEO_NAMES, 1):
            c = region_curve(z, yeo == i, g)
            if c is not None:
                networks[name] = c
    elif atlas is not None:
        for name, areas in YEO_FROM_HCP.items():
            c = region_curve(z, hcp_masks(atlas, areas), g)
            if c is not None:
                networks[name] = c
    curves: dict[str, np.ndarray | None] = {"stimulus_drive": drive}
    if {"dorsal_attention", "ventral_attention", "default"} <= set(networks):
        curves["engagement_index"] = (networks["dorsal_attention"] + networks["ventral_attention"]) / 2 - networks[
            "default"
        ]
    curves["anterior_insula"] = region_curve(z, hcp_masks(atlas, ANTERIOR_INSULA), g) if atlas else None
    hcp: dict[str, np.ndarray] = {}
    if atlas is not None:
        va = np.asarray(atlas["vertex_area"])
        for i, name in enumerate(atlas["areas"]):
            c = region_curve(z, va == i, g)
            if c is not None:
                hcp[name] = c
    return {"curves": curves, "networks": networks, "hcp": hcp, "global": g}


def _clean(a: np.ndarray | None) -> list | None:
    if a is None:
        return None
    return [None if not np.isfinite(v) else round(float(v), 5) for v in a]


def run(tribe_dir: Path, atlas_path: Path | None, cache_dir: Path | None, out_dir: Path) -> dict:
    preds = np.load(Path(tribe_dir) / "preds.npy")
    timing_path = Path(tribe_dir) / "timing.json"
    timing = json.loads(timing_path.read_text(encoding="utf-8")) if timing_path.exists() else {}
    t = timing.get("start_s") or list(range(preds.shape[0]))
    atlas = (
        json.loads(Path(atlas_path).read_text(encoding="utf-8")) if atlas_path and Path(atlas_path).exists() else None
    )
    yeo, yeo_source = load_yeo(cache_dir)
    if yeo is None and atlas is not None:
        yeo_source = f"approximated from HCP-MMP1 areas ({yeo_source})"
    result = compute(preds, atlas, yeo)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "t": t,
        "tr_s": timing.get("tr_s", 1.0),
        "curves": {k: _clean(v) for k, v in result["curves"].items()},
        "yeo7": {k: _clean(v) for k, v in result["networks"].items()},
        "nacc": None,
        "nacc_note": "unavailable: no released subcortical TRIBE v2 checkpoint",
        "definitions": {
            "region_curve": "z-score each vertex over time, average the region's vertices, regress out the "
            "whole-cortex average",
            "stimulus_drive": "RMS over vertices of the time-z-scored response",
            "engagement_index": "mean(dorsal_attention, ventral_attention) - default (draft)",
            "anterior_insula": "HCP-MMP1 AVI + AAIC + MI, bilateral",
        },
        "atlases": {
            "yeo7": yeo_source if (yeo is not None or atlas is not None) else None,
            "hcp": "HCP-MMP1 (Glasser 2016)" if atlas else None,
        },
    }
    (out_dir / "engagement.json").write_text(json.dumps(doc), encoding="utf-8")
    if result["hcp"]:
        np.savez_compressed(
            out_dir / "engagement_regions.npz",
            names=np.array(list(result["hcp"])),
            curves=np.stack(list(result["hcp"].values())).astype(np.float32),
        )

    def mean(key, n=None):
        v = result["curves"].get(key)
        if v is None:
            return None
        v = v[:n] if n else v
        return round(float(np.nanmean(v)), 5) if np.isfinite(v).any() else None

    summary = {
        "n_timesteps": int(preds.shape[0]),
        "yeo7_source": doc["atlases"]["yeo7"],
        "mean_engagement_index": mean("engagement_index"),
        "mean_engagement_index_first_30s": mean("engagement_index", 30),
        "mean_stimulus_drive": mean("stimulus_drive"),
        "mean_stimulus_drive_first_30s": mean("stimulus_drive", 30),
        "mean_anterior_insula": mean("anterior_insula"),
        "hcp_areas": len(result["hcp"]),
    }
    return summary
