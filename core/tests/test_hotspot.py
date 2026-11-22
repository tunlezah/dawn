"""The setup hotspot: a box that was never set up gets it after 45 s; one with a saved Wi-Fi network waits 15
minutes, keeps looking for its own network while the hotspot is up, and keeps its clock on the face. nmcli and iw
are a script; the clock is driven by hand."""

from __future__ import annotations

import pytest

from dawn_core.app import create_app
from dawn_core.config import ConfigManager
from dawn_core.face.service import FaceService
from dawn_core.net import service as netsvc
from dawn_core.net.nmcli import parse_ssids, parse_station_dump, parse_wifi_profiles, terse_value
from dawn_core.net.service import HOTSPOT_CON, NetworkService

PROFILES = "lo:loopback\nHome:802-11-wireless\ndawn-hotspot:802-11-wireless\nWired connection 1:802-3-ethernet\n"
STATION = "Station 3a:1f:00:11:22:33 (on wlan0)\n\tinactive time:\t120 ms\n\trx bytes:\t1234\n"


class FakeNm:
    """nmcli and iw as a script. `configured` decides whether a Wi-Fi profile is saved, `visible` what a scan
    sees, `client` whether a phone is on the hotspot, `join_rc` how `con up` ends."""

    def __init__(self) -> None:
        self.configured = True
        self.visible = {"Neighbour"}
        self.client = False
        self.join_rc = 0
        self.calls: list[tuple[str, ...]] = []

    async def run(self, *args: str, timeout: float = 15.0) -> tuple[int, str]:
        self.calls.append(args)
        if args[:2] == ("iw", "dev"):
            return 0, STATION if self.client else ""
        if args[1:] == ("-t", "-f", "NAME,TYPE", "con", "show"):
            return 0, PROFILES if self.configured else "lo:loopback\ndawn-hotspot:802-11-wireless\n"
        if "802-11-wireless.ssid" in args:
            return 0, "HomeWiFi\n"  # the profile "Home" is for the SSID "HomeWiFi"
        if args[1:4] == ("-t", "-f", "SSID"):
            return 0, "\n".join(sorted(self.visible)) + "\n"
        if args[1:3] == ("con", "up"):
            return self.join_rc, "Connection successfully activated" if self.join_rc == 0 else "Error: no network"
        return 0, ""

    def did(self, *prefix: str) -> bool:
        return any(c[: len(prefix)] == prefix for c in self.calls)

    def count(self, *prefix: str) -> int:
        return sum(1 for c in self.calls if c[: len(prefix)] == prefix)


@pytest.fixture()
async def net(tmp_config, monkeypatch):
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        ctx = app.state.ctx
        n: NetworkService = ctx.svc(NetworkService)
        n._task.cancel()  # polled by hand
        nm = FakeNm()
        monkeypatch.setattr(ctx, "sim", False)  # the fake nmcli plays the Pi
        monkeypatch.setattr(netsvc, "run", nm.run)
        monkeypatch.setattr(netsvc, "_ethernet_carrier", lambda: False)

        async def no_sleep(_s: float) -> None:
            return None

        n._sleep = no_sleep
        state = {"online": False}

        async def probe():
            # offline while the hotspot holds wlan0, whatever the router does: that is what made it latch
            if state["online"] and not n.hotspot_active:
                return True, "192.168.1.42", "HomeWiFi", "wlan0"
            return False, None, None, None

        n._probe = probe
        n._booted = 0.0
        yield n, ctx, nm, state


def _events(ctx, kind):
    return [e for e in ctx.db.recent_events(200) if e["kind"] == kind]


# ---- parsers ---------------------------------------------------------------------------------------
def test_parse_saved_wifi_profiles_leaves_out_the_hotspot() -> None:
    assert parse_wifi_profiles(PROFILES, HOTSPOT_CON) == ["Home"]
    assert parse_wifi_profiles("dawn-hotspot:802-11-wireless\nlo:loopback\n", HOTSPOT_CON) == []
    assert parse_wifi_profiles("Cafe\\:Guest:802-11-wireless\n", HOTSPOT_CON) == ["Cafe:Guest"]


def test_parse_ssids_station_dump_and_a_single_value() -> None:
    assert parse_ssids("HomeWiFi\n\nCafe\\:Guest\nHomeWiFi\n") == {"HomeWiFi", "Cafe:Guest"}
    assert parse_station_dump(STATION + STATION.replace("3a", "4b")) == 2
    assert parse_station_dump("") == 0
    assert terse_value("Cafe\\:Guest\n") == "Cafe:Guest"
    assert terse_value("") == ""


# ---- when the hotspot starts -----------------------------------------------------------------------
async def test_unconfigured_box_starts_the_hotspot_after_45_s_as_before(net) -> None:
    n, ctx, nm, _ = net
    nm.configured = False
    await n.poll(now=100.0)
    await n.poll(now=130.0)
    assert not n.hotspot_active
    await n.poll(now=146.0)
    assert n.hotspot_active and nm.did("nmcli", "dev", "wifi", "hotspot")
    assert ctx.store.state.system.network.wifi_configured is False
    ctx.svc(FaceService).recompute()
    assert ctx.store.state.face.mode == "setup"  # never set up: the full Setup screen, as before


async def test_configured_box_offline_for_a_minute_keeps_wlan0(net) -> None:
    n, ctx, nm, _ = net
    for t in (100.0, 130.0, 160.0):
        await n.poll(now=t)
    assert not n.hotspot_active
    assert not nm.did("nmcli", "dev", "wifi", "hotspot")
    assert ctx.store.state.system.network.wifi_configured is True


async def test_configured_box_offline_for_15_minutes_starts_the_hotspot(net) -> None:
    n, ctx, nm, _ = net
    await n.poll(now=100.0)
    await n.poll(now=100.0 + 899)
    assert not n.hotspot_active
    await n.poll(now=100.0 + 900)
    assert n.hotspot_active and ctx.store.state.system.network.hotspot_active
    assert len(_events(ctx, "hotspot_start")) == 1


# ---- the retry ---------------------------------------------------------------------------------------
async def _hotspot_up(n) -> float:
    await n.poll(now=100.0)
    await n.poll(now=1000.0)
    assert n.hotspot_active
    await n.poll(now=1001.0)  # the retry clock starts with the hotspot
    return 1001.0


async def test_retry_never_pulls_the_hotspot_from_under_a_phone(net) -> None:
    n, ctx, nm, _ = net
    t = await _hotspot_up(n)
    nm.client = True
    nm.visible = {"HomeWiFi"}
    await n.poll(now=t + 180)
    assert n.hotspot_active
    assert not nm.did("nmcli", "con", "down", HOTSPOT_CON)
    assert not nm.did("nmcli", "con", "up")
    assert [e["outcome"] for e in _events(ctx, "hotspot_retry")] == ["client connected"]


async def test_retry_joins_the_saved_network_when_it_is_in_view(net) -> None:
    n, ctx, nm, state = net
    t = await _hotspot_up(n)
    await n.poll(now=t + 179)
    assert not nm.did("nmcli", "con", "down", HOTSPOT_CON)  # not before retry_every_s
    nm.visible = {"Neighbour", "HomeWiFi"}  # the router is back
    state["online"] = True
    await n.poll(now=t + 180)
    assert nm.did("nmcli", "con", "down", HOTSPOT_CON) and nm.did("nmcli", "dev", "wifi", "rescan")
    assert ("nmcli", "con", "up", "id", "Home") in nm.calls  # by profile name, found through its SSID
    assert not n.hotspot_active and ctx.store.state.face.setup is None
    assert [e["outcome"] for e in _events(ctx, "hotspot_retry")] == ["joined"]
    await n.poll(now=t + 185)
    st = ctx.store.state.system.network
    assert st.online and st.ip == "192.168.1.42" and not st.hotspot_active


async def test_retry_puts_the_hotspot_back_when_the_network_is_not_in_view(net) -> None:
    n, ctx, nm, _ = net
    t = await _hotspot_up(n)
    starts = nm.count("nmcli", "dev", "wifi", "hotspot")
    await n.poll(now=t + 180)
    assert nm.did("nmcli", "con", "down", HOTSPOT_CON)
    assert not nm.did("nmcli", "con", "up")
    assert n.hotspot_active and nm.count("nmcli", "dev", "wifi", "hotspot") == starts + 1
    assert [e["outcome"] for e in _events(ctx, "hotspot_retry")] == ["no saved network in view"]
    assert len(_events(ctx, "hotspot_start")) == 1  # a retry is not a new hotspot in the event log
    await n.poll(now=t + 360)
    assert len(_events(ctx, "hotspot_retry")) == 2  # and it keeps looking


async def test_retry_puts_the_hotspot_back_when_joining_fails(net) -> None:
    n, ctx, nm, _ = net
    t = await _hotspot_up(n)
    nm.visible = {"HomeWiFi"}
    nm.join_rc = 4
    await n.poll(now=t + 180)
    assert n.hotspot_active
    assert [e["outcome"] for e in _events(ctx, "hotspot_retry")] == ["join failed"]


async def test_an_unconfigured_hotspot_is_never_retried(net) -> None:
    n, ctx, nm, _ = net
    nm.configured = False
    await n.poll(now=100.0)
    await n.poll(now=200.0)
    assert n.hotspot_active
    for t in (300.0, 600.0, 900.0):
        await n.poll(now=t)
    assert not nm.did("nmcli", "con", "down", HOTSPOT_CON)
    assert not _events(ctx, "hotspot_retry")


# ---- the face ---------------------------------------------------------------------------------------
async def test_a_configured_hotspot_at_night_keeps_the_sleep_clock(net) -> None:
    n, ctx, nm, _ = net
    face: FaceService = ctx.svc(FaceService)
    ctx.store.state.display.sleep = True
    await _hotspot_up(n)
    face.recompute()
    assert ctx.store.state.face.mode == "sleep"  # not a white QR code all night
    assert ctx.store.state.face.setup is not None  # the chip has what it needs
    ctx.store.state.display.sleep = False
    face.recompute()
    assert ctx.store.state.face.mode == "standby"


async def test_the_chip_opens_the_setup_screen_until_it_times_out_or_is_tapped(net) -> None:
    n, ctx, nm, _ = net
    face: FaceService = ctx.svc(FaceService)
    assert not face.open_setup(True)  # nothing to show without a hotspot
    await _hotspot_up(n)
    assert face.open_setup(True)
    assert ctx.store.state.face.mode == "setup"
    face.touch()  # a tap puts the clock back
    assert ctx.store.state.face.mode == "standby" and not ctx.store.state.face.setup_open
    assert face.open_setup(True)
    from datetime import timedelta

    face._setup_opened_at -= timedelta(seconds=ctx.config.inputs.touch_menu_timeout_s + 1)
    face._expire()
    assert ctx.store.state.face.mode == "standby"
