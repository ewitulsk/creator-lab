from runners.cpu.captions import cues_text, parse_vtt, pick_language
from runners.cpu.download import format_selector, needs_reencode

AUTO_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.160 --> 00:00:02.310 align:start position:0%

hey<00:00:00.480><c> everyone</c><00:00:00.800><c> welcome</c>

00:00:02.310 --> 00:00:02.320 align:start position:0%
hey everyone welcome


00:00:02.320 --> 00:00:04.950 align:start position:0%
hey everyone welcome
to<00:00:02.640><c> the</c><00:00:03.000><c> show</c>

00:00:04.950 --> 00:00:04.960 align:start position:0%
to the show


00:00:04.960 --> 00:01:07.500 align:start position:0%
to the show
today<00:00:05.300><c> we</c><00:00:05.600><c> build</c>
"""

UPLOADED_VTT = """WEBVTT

1
00:00:01.000 --> 00:00:03.000
Hello there.

2
01:00:03.500 --> 01:00:05.000
<i>Second</i> line.
"""


def test_parse_auto_captions_removes_rolling_duplicates():
    cues = parse_vtt(AUTO_VTT)
    assert [c["text"] for c in cues] == ["hey everyone welcome", "to the show", "today we build"]
    assert cues[0]["start"] == 0.16 and cues[-1]["end"] == 67.5
    assert cues_text(cues) == "hey everyone welcome to the show today we build"


def test_parse_uploaded_captions():
    cues = parse_vtt(UPLOADED_VTT)
    assert cues == [
        {"start": 1.0, "end": 3.0, "text": "Hello there."},
        {"start": 3603.5, "end": 3605.0, "text": "Second line."},
    ]


def test_pick_language_prefers_uploaded():
    info = {"subtitles": {"en": [{}]}, "automatic_captions": {"en": [{}], "de": [{}]}}
    assert pick_language(info, "en") == ("en", "uploaded")
    assert pick_language({"subtitles": {}, "automatic_captions": {"en": [{}]}}, None) == ("en", "auto")
    assert pick_language({"subtitles": {"en-GB": [{}]}, "automatic_captions": {}}, "en") == ("en-GB", "uploaded")
    assert pick_language({"subtitles": {}, "automatic_captions": {"fr-orig": [{}]}}, "fr") == ("fr-orig", "auto")
    assert pick_language({}, "en") == (None, None)


def test_format_selector_never_exceeds_cap():
    sel = format_selector(1080)
    for alternative in sel.split("/"):
        assert "height<=1080" in alternative
    assert "2160" not in format_selector(720)


def test_needs_reencode():
    assert not needs_reencode({"vcodec": "h264", "acodec": "aac", "height": 1080}, 1080)
    assert needs_reencode({"vcodec": "av1", "acodec": "aac", "height": 1080}, 1080)
    assert needs_reencode({"vcodec": "vp9", "acodec": "opus", "height": 720}, 1080)
    assert needs_reencode({"vcodec": "h264", "acodec": "aac", "height": 2160}, 1080)
