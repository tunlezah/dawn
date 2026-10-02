from __future__ import annotations

import base64

from dawn_core.airplay.metadata import AirPlayTrack, MetadataParser, artwork_mime


def item(typ: str, code: str, data: bytes | None = None) -> bytes:
    t = typ.encode().hex()
    c = code.encode().hex()
    if data is None:
        return f"<item><type>{t}</type><code>{c}</code><length>0</length></item>\n".encode()
    b64 = base64.b64encode(data).decode()
    return f"<item><type>{t}</type><code>{c}</code><length>{len(data)}</length>\n<data encoding=\"base64\">\n{b64}</data></item>\n".encode()


def test_parse_items_and_partial_feeds() -> None:
    p = MetadataParser()
    raw = item("ssnc", "pbeg") + item("core", "minm", "Golden Hour".encode()) + item("core", "asar", "Kacey Musgraves".encode())
    items = p.feed(raw[:40]) + p.feed(raw[40:])
    assert [(i.type, i.code) for i in items] == [("ssnc", "pbeg"), ("core", "minm"), ("core", "asar")]
    assert items[1].text == "Golden Hour"


def test_track_state_machine() -> None:
    p = MetadataParser()
    t = AirPlayTrack()
    events = []
    raw = item("ssnc", "snam", b"Sam's iPhone") + item("ssnc", "pbeg") + item("core", "minm", b"Sunrise") + item("core", "asal", b"Come Away With Me")
    raw += item("ssnc", "PICT", b"\xff\xd8\xff\xe0JFIF") + item("ssnc", "pfls") + item("ssnc", "prsm") + item("ssnc", "pend")
    for it in p.feed(raw):
        ev = t.apply(it)
        if ev:
            events.append(ev)
        if ev == "artwork":
            assert artwork_mime(t.artwork) == "image/jpeg" and t.title == "Sunrise" and t.client == "Sam's iPhone"
    assert events == ["begin", "artwork", "pause", "resume", "end"]
    assert not t.session and t.title is None and t.artwork is None


def test_artwork_mime() -> None:
    assert artwork_mime(b"\x89PNG\r\n\x1a\nxxx") == "image/png"
    assert artwork_mime(b"nope") == "application/octet-stream"


def test_progress_from_prgr_and_astm() -> None:
    p = MetadataParser()
    t = AirPlayTrack()
    raw = item("ssnc", "pbeg") + item("core", "minm", b"Sunrise") + item("core", "astm", (224_000).to_bytes(4, "big"))
    raw += item("ssnc", "prgr", b"1000000/5498200/10878400")
    for it in p.feed(raw):
        t.apply(it)
    assert t.duration_s == 224.0
    assert t.position_s is not None and abs(t.position_s - 102.0) < 0.01
    assert t.position_at is not None and t.position_now() >= 102.0
    # pause freezes, resume continues, new title clears, end clears
    for it in p.feed(item("ssnc", "pfls")):
        t.apply(it)
    assert t.position_at is None and t.position_now() == t.position_s
    for it in p.feed(item("ssnc", "prsm")):
        t.apply(it)
    assert t.position_at is not None
    for it in p.feed(item("core", "minm", b"Next song")):
        t.apply(it)
    assert t.position_s is None and t.duration_s is None
    for it in p.feed(item("ssnc", "prgr", b"bad")):
        t.apply(it)
    assert t.position_s is None
