from runners.cpu import ytdl


def test_base_opts_enables_first_runtime_on_path(monkeypatch):
    paths = {"node": "/usr/bin/node", "bun": "/usr/bin/bun"}
    monkeypatch.setattr(ytdl.shutil, "which", lambda name: paths.get(name))
    opts = ytdl.base_opts(skip_download=True)
    assert opts["js_runtimes"] == {"node": {"path": "/usr/bin/node"}}
    assert opts["skip_download"] is True and opts["quiet"] is True


def test_base_opts_without_runtime(monkeypatch):
    monkeypatch.setattr(ytdl.shutil, "which", lambda name: None)
    assert "js_runtimes" not in ytdl.base_opts()


def test_runtime_names_are_ones_yt_dlp_supports():
    import yt_dlp  # noqa: F401  (registers the runtimes)
    from yt_dlp.globals import supported_js_runtimes

    assert set(ytdl.RUNTIMES) <= set(supported_js_runtimes.value)
