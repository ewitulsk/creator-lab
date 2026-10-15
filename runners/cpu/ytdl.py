"""Shared yt-dlp options.

YouTube's player now needs a JavaScript challenge solved before media URLs work (without it,
downloads fail with HTTP 403 or lose formats). yt-dlp solves it with the yt-dlp-ejs scripts
(installed by the yt-dlp[default] extra) and an external JS runtime. yt-dlp only enables deno
by default, so enable whichever supported runtime is on PATH.
"""

from __future__ import annotations

import shutil

RUNTIMES = ("deno", "node", "bun")


def js_runtimes() -> dict[str, dict]:
    for name in RUNTIMES:
        exe = shutil.which(name)
        if exe:
            return {name: {"path": exe}}
    return {}


def base_opts(**extra) -> dict:
    opts: dict = {"quiet": True, "no_warnings": True}
    runtimes = js_runtimes()
    if runtimes:
        opts["js_runtimes"] = runtimes
    return opts | extra
