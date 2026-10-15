import numpy as np
import pytest

from runners.tribe.brain_assets import pack_mesh


def _hemi(n=4):
    coords = np.arange(n * 3, dtype=np.float64).reshape(n, 3)
    faces = np.array([[0, 1, 2], [1, 2, 3]], dtype=np.int64)
    return {"coords": coords, "faces": faces, "sulc": np.linspace(-1, 1, n)}


def test_pack_mesh_layout():
    blob, meta = pack_mesh({"left": _hemi(), "right": _hemi()})
    assert meta["bytes"] == len(blob)
    left = meta["hemis"][0]
    assert left["name"] == "left" and left["n_vertices"] == 4 and left["n_faces"] == 2
    coords = np.frombuffer(blob, np.float32, left["coords"]["length"], left["coords"]["offset"]).reshape(4, 3)
    np.testing.assert_allclose(coords, _hemi()["coords"])
    right = meta["hemis"][1]
    faces = np.frombuffer(blob, np.uint32, right["faces"]["length"], right["faces"]["offset"]).reshape(2, 3)
    assert faces.tolist() == [[0, 1, 2], [1, 2, 3]]
    assert left["bbox"] == [[0.0, 1.0, 2.0], [9.0, 10.0, 11.0]]


def test_pack_mesh_validates():
    bad = _hemi()
    bad["faces"] = np.array([[0, 1, 9]])
    with pytest.raises(ValueError):
        pack_mesh({"left": bad, "right": _hemi()})
