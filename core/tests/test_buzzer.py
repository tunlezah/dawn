"""The backup tone: generated beeps, the order players are tried in, moving on when one fails, the GPIO buzzer."""

from __future__ import annotations

import asyncio
import io
import wave
from types import SimpleNamespace

import pytest

from dawn_core.alarms import buzzer as bz
from dawn_core.config.schema import DawnConfig

CARDS = """ 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0
                      vc4-hdmi-0
 1 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones
                      bcm2835 Headphones
 2 [sndrpihifiberry]: HifiberryDac - snd_rpi_hifiberry_dac
                      snd_rpi_hifiberry_dac
"""


def test_beep_wav_is_a_short_mono_pattern() -> None:
    data = bz.beep_wav()
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, bz.RATE)
        seconds = w.getnframes() / w.getframerate()
        frames = w.readframes(w.getnframes())
    assert seconds == pytest.approx(bz.CYCLES * (bz.BEEPS * (bz.BEEP_S + bz.GAP_S) + bz.PAUSE_S), abs=0.01)
    peak = max(abs(int.from_bytes(frames[i:i + 2], "little", signed=True)) for i in range(0, 40_000, 2))
    assert 10_000 < peak < 32_767  # loud, but not clipping into the filter chain


def test_cards_put_the_amp_first_and_hdmi_last() -> None:
    assert bz.alsa_cards(CARDS) == ["sndrpihifiberry", "Headphones", "vc4hdmi0"]
    assert bz.alsa_cards("") == []


def test_player_commands_try_pipewire_then_alsa_then_each_card() -> None:
    have = {"pw-play", "aplay"}
    cmds = bz.player_commands("/run/t.wav", which=lambda n: f"/usr/bin/{n}" if n in have else None, cards=["sndrpihifiberry", "vc4hdmi0"])
    assert cmds == [
        ["pw-play", "/run/t.wav"],
        ["aplay", "-q", "/run/t.wav"],
        ["aplay", "-q", "-D", "plughw:CARD=sndrpihifiberry,DEV=0", "/run/t.wav"],
        ["aplay", "-q", "-D", "plughw:CARD=vc4hdmi0,DEV=0", "/run/t.wav"],
    ]
    assert bz.player_commands("/run/t.wav", which=lambda n: None, cards=[]) == []


def _ctx(tmp_path, sim: bool = False):
    return SimpleNamespace(config=DawnConfig(), sim=sim, runtime_dir=tmp_path)


async def test_buzzer_moves_on_from_a_failing_player_and_keeps_the_good_one(tmp_path, monkeypatch) -> None:
    # "false" fails at once (PipeWire down); the sh line plays the "file" for 1.2 s and succeeds
    good = ["sh", "-c", "sleep 1.2", "player"]
    monkeypatch.setattr(bz, "player_commands", lambda wav, **kw: [["false", wav], [*good[:-1], wav]])
    b = bz.Buzzer(_ctx(tmp_path))  # type: ignore[arg-type]
    await b.start()
    for _ in range(100):
        if b.sounding:
            break
        await asyncio.sleep(0.05)
    assert b.sounding and b.player and b.player.startswith("sh")
    assert (tmp_path / "dawn-backup-tone.wav").exists()
    await asyncio.sleep(1.5)  # the first round played through: the good player goes first now
    assert b._good and b._good[0] == "sh"
    await b.stop()
    assert not b.active and not b.sounding and b._proc is None


async def test_buzzer_with_no_working_player_says_so_and_keeps_trying(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(bz, "player_commands", lambda wav, **kw: [["false", wav]])
    b = bz.Buzzer(_ctx(tmp_path))  # type: ignore[arg-type]
    await b.start()
    await asyncio.sleep(0.5)
    assert b.active and not b.sounding and "no program could play" in (b.problem or "")
    await b.stop()


async def test_buzzer_without_any_player_program(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(bz, "player_commands", lambda wav, **kw: [])
    b = bz.Buzzer(_ctx(tmp_path))  # type: ignore[arg-type]
    await b.start()
    await asyncio.sleep(0.2)
    assert "none of pw-play" in (b.problem or "")
    await b.stop()


async def test_gpio_buzzer_beeps_the_pattern_and_goes_quiet() -> None:
    from gpiozero import Device
    from gpiozero.pins.mock import MockFactory

    Device.pin_factory = MockFactory()
    try:
        g = bz.GpioBuzzer()
        g.start(26, True)
        pin = Device.pin_factory.pin(26)
        seen = set()
        for _ in range(30):
            seen.add(pin.state)
            await asyncio.sleep(0.02)
        assert seen == {0, 1} and g.beeping  # on and off: beeping
        g.stop()
        await asyncio.sleep(0.05)
        assert pin.state == 0 and not g.beeping
        g.close()
    finally:
        Device.pin_factory.reset()
        Device.pin_factory = None


def test_buzzer_pin_counts_towards_the_unique_pins() -> None:
    with pytest.raises(ValueError, match="unique"):
        DawnConfig.model_validate({"alarm_defaults": {"buzzer": {"gpio_pin": 22}}})  # the encoder switch
    assert DawnConfig.model_validate({"alarm_defaults": {"buzzer": {"gpio_pin": 26}}}).alarm_defaults.buzzer.gpio_pin == 26
