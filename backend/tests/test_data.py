"""Compare creators, Shorts list, exports and backups (synthetic rows only)."""

from __future__ import annotations

import csv
import datetime as dt
import io

import pandas as pd

from creator_lab.extensions import db
from creator_lab.models import Channel, ChannelSnapshot, Video, VideoSnapshot


def _seed():
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    ids = []
    for n, title in enumerate(("Alpha", "Beta")):
        ch = Channel(platform_id=1, external_id=f"UC{title:x<22}"[:24], title=title)
        db.session.add(ch)
        db.session.flush()
        db.session.add(
            ChannelSnapshot(channel_id=ch.id, captured_at=now, subscribers=1000 * (n + 1), views=5, video_count=6)
        )
        for i in range(6):
            fmt = "short" if i % 3 == 0 else "long"
            v = Video(
                channel_id=ch.id,
                platform_id=1,
                external_id=f"{title}{i}",
                title=f"{title} video {i}",
                format=fmt,
                duration_s=40 if fmt == "short" else 600,
                published_at=now - dt.timedelta(days=20 * i + 1),
                views=1000 * (i + 1),
                likes=10 * (i + 1),
                comments=i + 1,
                outlier_score=[0.4, 1.0, 2.5, 1.1, 3.0, 0.9][i],
            )
            db.session.add(v)
            db.session.flush()
            db.session.add(
                VideoSnapshot(video_id=v.id, captured_at=now, views=v.views, likes=v.likes, comments=v.comments)
            )
        ids.append(ch.id)
    db.session.commit()
    return ids


def test_compare(client):
    a, b = _seed()
    r = client.get(f"/api/compare?channel_ids={a},{b}&months=12")
    assert r.status_code == 200, r.json
    chans = r.json["channels"]
    assert [c["title"] for c in chans] == ["Alpha", "Beta"]
    long = chans[0]["long"]
    assert long["count"] == 4 and long["recent_count"] == 4
    assert long["hit_rate"] == 0.5  # two of the four long uploads scored >= 2
    assert chans[0]["short"]["count"] == 2
    assert chans[0]["shorts_share"] == round(2 / 6, 4)
    assert chans[0]["subscribers"][0]["subscribers"] == 1000
    assert sum(m["long"] + m["short"] for m in chans[0]["monthly"]) == 6
    assert client.get("/api/compare?channel_ids=").status_code == 400
    assert client.get("/api/compare?channel_ids=999").status_code == 404


def test_shorts(client):
    a, _ = _seed()
    r = client.get("/api/shorts?sort=outlier_score")
    assert r.status_code == 200
    assert r.json["total"] == 4
    assert all(v["format"] == "short" for v in r.json["videos"])
    assert r.json["videos"][0]["outlier_score"] == 1.1
    assert client.get(f"/api/shorts?channel_id={a}").json["total"] == 2
    assert client.get("/api/shorts?sort=nope").status_code == 400


def test_exports(client):
    a, _ = _seed()
    r = client.get(f"/api/exports/videos.csv?channel_id={a}")
    assert r.status_code == 200
    rows = list(csv.DictReader(io.StringIO(r.get_data(as_text=True))))
    assert len(rows) == 6 and rows[0]["channel"] == "Alpha"
    r = client.get("/api/exports/video_snapshots.parquet")
    df = pd.read_parquet(io.BytesIO(r.data))
    assert len(df) == 12 and "views" in df.columns
    r = client.get("/api/exports/channel_snapshots.csv")
    assert len(list(csv.DictReader(io.StringIO(r.get_data(as_text=True))))) == 2
    assert client.get("/api/exports/analyzer.csv?analyzer=outlier_score").status_code == 200
    assert client.get("/api/exports/analyzer.csv?analyzer=nope").status_code == 400
    assert client.get("/api/exports/bogus.csv").status_code == 404
    assert client.get("/api/exports/videos.xlsx").status_code == 404


def test_backups(client, app):
    from creator_lab import backups
    from creator_lab.settings import update_settings

    # The test app's database is a file under DATA_ROOT, so the online backup API works on it.
    _seed()
    assert backups.due()
    r = client.post("/api/backups")
    assert r.status_code == 200, r.json
    name = r.json["backup"]["name"]
    assert name.startswith("creator_lab-") and r.json["backup"]["bytes"] > 0
    assert not backups.due()
    d = client.get(f"/api/backups/{name}")
    assert d.status_code == 200 and d.data[:15] == b"SQLite format 3"
    assert client.get("/api/backups/..%2Fdb%2Fcreator_lab.sqlite").status_code == 404
    # Pruning keeps the newest N.
    folder = backups.backups_dir()
    for i in range(4):
        (folder / f"creator_lab-20000101-00000{i}.sqlite").write_bytes(b"x")
    update_settings({"backups_to_keep": 2})
    db.session.commit()
    removed = backups.prune(2)
    assert len(removed) == 3
    assert [b["name"] for b in client.get("/api/backups").json["backups"]][0] == name
