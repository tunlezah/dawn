from __future__ import annotations

import asyncio

from dawn_core.audio.arbiter import Arbiter
from dawn_core.audio.sources import AudioSource


class FakeSource(AudioSource):
    kind = "url"  # type: ignore[assignment]

    def __init__(self, name: str):
        super().__init__()
        self.label = name
        self.ref = f"fake:{name}"
        self.log: list[str] = []
        self._playing = False
        self.gain = 100.0

    async def start(self) -> None:
        self.log.append("start")
        self._playing = True

    async def stop(self) -> None:
        self.log.append("stop")
        self._playing = False

    async def pause(self) -> None:
        self.log.append("pause")
        self._playing = False

    async def resume(self) -> None:
        self.log.append("resume")
        self._playing = True

    async def set_gain(self, percent: float) -> None:
        self.gain = percent
        self.log.append(f"gain={int(percent)}")

    @property
    def playing(self) -> bool:
        return self._playing


async def test_alarm_preempts_user_and_user_resumes() -> None:
    arb = Arbiter(duck_percent=20, duck_seconds=0.05)
    radio = FakeSource("radio")
    alarm = FakeSource("alarm")
    await arb.acquire("user", radio)
    assert radio.playing and arb.active is not None and arb.active.level == "user"

    await arb.acquire("alarm", alarm)
    assert arb.active.level == "alarm"
    assert radio.gain == 20  # ducked immediately
    await asyncio.sleep(0.1)
    assert not radio.playing and "pause" in radio.log  # then paused
    assert arb.slot("user").state == "paused"

    await arb.release("alarm")
    assert "stop" in alarm.log
    assert radio.playing and radio.gain == 100
    assert arb.active.level == "user"


async def test_lower_priority_waits_behind_higher() -> None:
    arb = Arbiter(duck_seconds=0.01)
    alarm = FakeSource("alarm")
    bt = FakeSource("bt")
    await arb.acquire("alarm", alarm)
    await arb.acquire("bluetooth", bt)
    assert not bt.playing and arb.slot("bluetooth").state == "paused"
    await arb.release("alarm")
    assert bt.playing


async def test_priority_chain_airplay_over_bluetooth_under_user() -> None:
    arb = Arbiter(duck_seconds=0.01)
    bt, ap, user = FakeSource("bt"), FakeSource("airplay"), FakeSource("user")
    await arb.acquire("bluetooth", bt)
    await arb.acquire("airplay", ap)
    await asyncio.sleep(0.03)
    assert ap.playing and not bt.playing
    await arb.acquire("user", user)
    await asyncio.sleep(0.03)
    assert user.playing and not ap.playing and not bt.playing
    await arb.release("user")
    assert ap.playing and not bt.playing  # only the next highest resumes
    await arb.release("airplay")
    assert bt.playing


async def test_source_not_playing_before_is_not_resumed() -> None:
    arb = Arbiter(duck_seconds=0.01)
    radio, alarm = FakeSource("radio"), FakeSource("alarm")
    await arb.acquire("user", radio)
    await arb.pause_level("user")  # user paused it themselves
    assert not radio.playing
    await arb.acquire("alarm", alarm)
    await arb.release("alarm")
    assert not radio.playing  # stays paused: it was not playing before the alarm


async def test_replace_same_level_stops_previous() -> None:
    arb = Arbiter(duck_seconds=0.01)
    a, b = FakeSource("a"), FakeSource("b")
    await arb.acquire("user", a)
    await arb.acquire("user", b)
    assert "stop" in a.log and b.playing
    assert len(arb.slots) == 1


async def test_release_all_below() -> None:
    arb = Arbiter(duck_seconds=0.01)
    await arb.acquire("bluetooth", FakeSource("bt"))
    await arb.acquire("alarm", FakeSource("al"))
    await arb.release_all(below=100)
    assert list(arb.slots) == ["alarm"]
