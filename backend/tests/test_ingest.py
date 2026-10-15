import pytest

from creator_lab import ingest
from creator_lab.youtube.client import parse_channel_input

from .fake_youtube import CID


@pytest.mark.parametrize(
    "text,expected",
    [
        ("@veritasium", ("handle", "@veritasium")),
        ("veritasium", ("handle", "@veritasium")),
        (CID, ("id", CID)),
        (f"https://www.youtube.com/channel/{CID}/videos", ("id", CID)),
        ("https://www.youtube.com/@mkbhd/videos", ("handle", "@mkbhd")),
        ("youtube.com/@mkbhd", ("handle", "@mkbhd")),
        ("https://www.youtube.com/user/oldname", ("username", "oldname")),
        ("https://www.youtube.com/c/custom", ("url", "https://www.youtube.com/c/custom")),
    ],
)
def test_parse_channel_input(text, expected):
    assert parse_channel_input(text) == expected


def test_parse_duration():
    assert ingest.parse_duration("PT1H2M3S") == 3723
    assert ingest.parse_duration("PT45S") == 45
    assert ingest.parse_duration("P1DT1S") == 86401
    assert ingest.parse_duration("P0D") == 0
    assert ingest.parse_duration(None) is None


def test_classify_format():
    item = {"id": "x", "snippet": {}}
    assert ingest.classify_format(item, 600, 1.77) == "long"
    assert ingest.classify_format(item, 50, 0.56) == "short"
    assert ingest.classify_format(item, 170, 1.77) == "long"  # horizontal, under 3 min
    assert ingest.classify_format(item, 170, 0.56, lambda _id: False) == "long"  # vertical but not a Short
    assert ingest.classify_format({"id": "x", "liveStreamingDetails": {}, "snippet": {}}, 3600, 1.77) == "live"
