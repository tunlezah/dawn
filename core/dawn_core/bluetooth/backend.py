"""Bluetooth backends: BlueZ over D-Bus (dbus-fast) and a simulator (hub-driven).

The backend reports devices, adapter state and the A2DP player state; the
service maps that onto the arbiter and the face.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("dawn.bt.backend")


@dataclass
class BtDeviceInfo:
    address: str
    name: str = ""
    paired: bool = False
    connected: bool = False
    trusted: bool = False
    icon: str | None = None
    rssi: int | None = None
    path: str | None = None


@dataclass
class BtSnapshot:
    available: bool = False
    powered: bool = False
    discoverable: bool = False
    scanning: bool = False
    alias: str = ""
    devices: list[BtDeviceInfo] = field(default_factory=list)
    connected: str | None = None  # address
    player_status: str | None = None  # playing | paused | stopped
    transport_active: bool = False
    track: dict[str, Any] = field(default_factory=dict)  # Title, Artist, Album, Duration


ChangeCb = Callable[[], Awaitable[None] | None]


class BluetoothBackend:
    name = "none"

    def __init__(self, on_change: ChangeCb):
        self.on_change = on_change
        self.snap = BtSnapshot()

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def set_alias(self, name: str) -> None: ...
    async def set_discoverable(self, on: bool, timeout_s: int) -> None: ...
    async def scan(self, on: bool) -> None: ...
    async def pair(self, address: str) -> None: ...
    async def connect(self, address: str) -> None: ...
    async def disconnect(self, address: str) -> None: ...
    async def remove(self, address: str) -> None: ...
    async def player(self, command: str) -> None: ...

    def _changed(self) -> None:
        r = self.on_change()
        if asyncio.iscoroutine(r):
            asyncio.ensure_future(r)


# --------------------------------------------------------------------------- #
# BlueZ
# --------------------------------------------------------------------------- #
def _unpack(v: Any) -> Any:
    from dbus_fast import Variant  # type: ignore

    if isinstance(v, Variant):
        return _unpack(v.value)
    if isinstance(v, dict):
        return {k: _unpack(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_unpack(x) for x in v]
    return v


class BluezBackend(BluetoothBackend):
    name = "bluez"
    BLUEZ = "org.bluez"

    def __init__(self, on_change: ChangeCb, adapter: str = "hci0"):
        super().__init__(on_change)
        self.adapter = adapter
        self.adapter_path = f"/org/bluez/{adapter}"
        self.bus = None
        self._objects: dict[str, dict[str, dict[str, Any]]] = {}
        self._agent_path = "/org/dawn/agent"
        self._refresh_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        from dbus_fast import BusType, Message  # type: ignore
        from dbus_fast.aio import MessageBus  # type: ignore

        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        for rule in (
            "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.Properties',member='PropertiesChanged'",
            "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.ObjectManager'",
        ):
            await self.bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus", interface="org.freedesktop.DBus", member="AddMatch", signature="s", body=[rule]))
        self.bus.add_message_handler(self._on_signal)
        await self._register_agent()
        await self.refresh()
        self.snap.available = self.adapter_path in self._objects
        log.info("bluez backend ready (adapter %s %s)", self.adapter, "present" if self.snap.available else "missing")

    async def stop(self) -> None:
        if self.bus:
            self.bus.disconnect()

    # ---- D-Bus helpers ---------------------------------------------------
    async def _call(self, path: str, iface: str, member: str, signature: str = "", body: list | None = None, timeout: float = 20.0) -> Any:
        from dbus_fast import Message, MessageType  # type: ignore

        msg = Message(destination=self.BLUEZ, path=path, interface=iface, member=member, signature=signature, body=body or [])
        reply = await asyncio.wait_for(self.bus.call(msg), timeout)
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"{member}: {reply.error_name} {reply.body}")
        return _unpack(reply.body)

    async def _set(self, path: str, iface: str, prop: str, sig: str, value: Any) -> None:
        from dbus_fast import Variant  # type: ignore

        await self._call(path, "org.freedesktop.DBus.Properties", "Set", "ssv", [iface, prop, Variant(sig, value)])

    async def refresh(self) -> None:
        objs = await self._call("/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
        self._objects = objs[0] if isinstance(objs, list) else objs
        self._rebuild()

    def _rebuild(self) -> None:
        s = self.snap
        ad = self._objects.get(self.adapter_path, {}).get("org.bluez.Adapter1")
        s.available = ad is not None
        if ad:
            s.powered = bool(ad.get("Powered"))
            s.discoverable = bool(ad.get("Discoverable"))
            s.scanning = bool(ad.get("Discovering"))
            s.alias = str(ad.get("Alias") or "")
        devices = []
        s.connected = None
        s.player_status = None
        s.transport_active = False
        s.track = {}
        for path, ifaces in self._objects.items():
            dev = ifaces.get("org.bluez.Device1")
            if dev and path.startswith(self.adapter_path):
                d = BtDeviceInfo(address=str(dev.get("Address", "")), name=str(dev.get("Alias") or dev.get("Name") or ""), paired=bool(dev.get("Paired")),
                                 connected=bool(dev.get("Connected")), trusted=bool(dev.get("Trusted")), icon=dev.get("Icon"), rssi=dev.get("RSSI"), path=path)
                devices.append(d)
                if d.connected:
                    s.connected = d.address
            player = ifaces.get("org.bluez.MediaPlayer1")
            if player:
                s.player_status = str(player.get("Status") or "")
                s.track = dict(player.get("Track") or {})
            transport = ifaces.get("org.bluez.MediaTransport1")
            if transport and str(transport.get("State")) == "active":
                s.transport_active = True
        s.devices = sorted(devices, key=lambda d: (not d.connected, not d.paired, d.name))

    def _on_signal(self, msg: Any) -> None:
        from dbus_fast import MessageType  # type: ignore

        if msg.message_type != MessageType.SIGNAL:
            return
        if msg.interface == "org.freedesktop.DBus.Properties" and msg.member == "PropertiesChanged":
            iface, changed, _inv = msg.body
            obj = self._objects.setdefault(msg.path, {})
            obj.setdefault(iface, {}).update(_unpack(changed))
            self._rebuild()
            self._changed()
        elif msg.interface == "org.freedesktop.DBus.ObjectManager":
            if msg.member == "InterfacesAdded":
                path, ifaces = msg.body
                self._objects.setdefault(path, {}).update(_unpack(ifaces))
            elif msg.member == "InterfacesRemoved":
                path, ifaces = msg.body
                for i in ifaces:
                    self._objects.get(path, {}).pop(i, None)
                if not self._objects.get(path):
                    self._objects.pop(path, None)
            self._rebuild()
            self._changed()

    async def _register_agent(self) -> None:
        """Just-works agent so phones can pair while Dawn is discoverable."""
        try:
            from dbus_fast.service import ServiceInterface, method  # type: ignore

            class Agent(ServiceInterface):
                def __init__(self) -> None:
                    super().__init__("org.bluez.Agent1")

                @method()
                def Release(self) -> None:
                    pass

                @method()
                def RequestPinCode(self, device: o) -> s:  # noqa: F821
                    return "0000"

                @method()
                def RequestPasskey(self, device: o) -> u:  # noqa: F821
                    return 0

                @method()
                def DisplayPasskey(self, device: o, passkey: u, entered: q) -> None:  # noqa: F821
                    pass

                @method()
                def DisplayPinCode(self, device: o, pincode: s) -> None:  # noqa: F821
                    pass

                @method()
                def RequestConfirmation(self, device: o, passkey: u) -> None:  # noqa: F821
                    log.info("bluetooth pairing confirmation auto-accepted for %s", device)

                @method()
                def RequestAuthorization(self, device: o) -> None:  # noqa: F821
                    pass

                @method()
                def AuthorizeService(self, device: o, uuid: s) -> None:  # noqa: F821
                    pass

                @method()
                def Cancel(self) -> None:
                    pass

            self.bus.export(self._agent_path, Agent())
            await self._call("/org/bluez", "org.bluez.AgentManager1", "RegisterAgent", "os", [self._agent_path, "NoInputNoOutput"])
            await self._call("/org/bluez", "org.bluez.AgentManager1", "RequestDefaultAgent", "o", [self._agent_path])
        except Exception as e:  # noqa: BLE001
            log.warning("bluetooth agent registration failed: %s", e)

    def _dev_path(self, address: str) -> str:
        for d in self.snap.devices:
            if d.address.lower() == address.lower() and d.path:
                return d.path
        return f"{self.adapter_path}/dev_{address.replace(':', '_').upper()}"

    # ---- operations ------------------------------------------------------
    async def set_alias(self, name: str) -> None:
        await self._set(self.adapter_path, "org.bluez.Adapter1", "Alias", "s", name)

    async def set_discoverable(self, on: bool, timeout_s: int) -> None:
        await self._set(self.adapter_path, "org.bluez.Adapter1", "Powered", "b", True)
        await self._set(self.adapter_path, "org.bluez.Adapter1", "DiscoverableTimeout", "u", timeout_s if on else 0)
        await self._set(self.adapter_path, "org.bluez.Adapter1", "Pairable", "b", on)
        await self._set(self.adapter_path, "org.bluez.Adapter1", "Discoverable", "b", on)

    async def scan(self, on: bool) -> None:
        await self._call(self.adapter_path, "org.bluez.Adapter1", "StartDiscovery" if on else "StopDiscovery")

    async def pair(self, address: str) -> None:
        p = self._dev_path(address)
        await self._call(p, "org.bluez.Device1", "Pair", timeout=60)
        await self._set(p, "org.bluez.Device1", "Trusted", "b", True)
        await self._call(p, "org.bluez.Device1", "Connect", timeout=30)

    async def connect(self, address: str) -> None:
        await self._call(self._dev_path(address), "org.bluez.Device1", "Connect", timeout=30)

    async def disconnect(self, address: str) -> None:
        await self._call(self._dev_path(address), "org.bluez.Device1", "Disconnect")

    async def remove(self, address: str) -> None:
        await self._call(self.adapter_path, "org.bluez.Adapter1", "RemoveDevice", "o", [self._dev_path(address)])

    async def player(self, command: str) -> None:
        for path, ifaces in self._objects.items():
            if "org.bluez.MediaPlayer1" in ifaces:
                try:
                    await self._call(path, "org.bluez.MediaPlayer1", command, timeout=5)
                except Exception as e:  # noqa: BLE001
                    log.debug("player %s failed: %s", command, e)
                return


# --------------------------------------------------------------------------- #
# Simulator
# --------------------------------------------------------------------------- #
class SimBluetoothBackend(BluetoothBackend):
    name = "sim"

    def __init__(self, on_change: ChangeCb, hub_url: str):
        super().__init__(on_change)
        self.hub = hub_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=3.0)
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll(), name="sim-bt")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self._client.aclose()

    async def _poll(self) -> None:
        while True:
            try:
                r = await self._client.get(f"{self.hub}/bluetooth/state")
                j = r.json()
                old = (self.snap.connected, self.snap.player_status, self.snap.transport_active, str(self.snap.track), len(self.snap.devices), self.snap.discoverable)
                self.snap = BtSnapshot(
                    available=True, powered=True, discoverable=bool(j.get("discoverable")), scanning=False, alias=j.get("alias", "Dawn"),
                    devices=[BtDeviceInfo(**{k: v for k, v in d.items() if k in BtDeviceInfo.__dataclass_fields__}) for d in j.get("devices", [])],
                    connected=j.get("connected"), player_status=j.get("player_status"), transport_active=bool(j.get("transport_active")), track=j.get("track") or {},
                )
                if old != (self.snap.connected, self.snap.player_status, self.snap.transport_active, str(self.snap.track), len(self.snap.devices), self.snap.discoverable):
                    self._changed()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(1.0)

    async def _post(self, path: str, **body: Any) -> None:
        try:
            await self._client.post(f"{self.hub}/bluetooth/{path}", json=body)
        except Exception:  # noqa: BLE001
            pass

    async def set_alias(self, name: str) -> None:
        await self._post("alias", name=name)

    async def set_discoverable(self, on: bool, timeout_s: int) -> None:
        await self._post("discoverable", on=on, timeout_s=timeout_s)

    async def scan(self, on: bool) -> None:
        await self._post("scan", on=on)

    async def pair(self, address: str) -> None:
        await self._post("pair", address=address)

    async def connect(self, address: str) -> None:
        await self._post("connect", address=address)

    async def disconnect(self, address: str) -> None:
        await self._post("disconnect", address=address)

    async def remove(self, address: str) -> None:
        await self._post("remove", address=address)

    async def player(self, command: str) -> None:
        await self._post("player", command=command)


async def make_backend(on_change: ChangeCb, sim: bool, hub_url: str, adapter: str) -> BluetoothBackend:
    if sim:
        return SimBluetoothBackend(on_change, hub_url)
    try:
        import dbus_fast  # noqa: F401
    except ImportError:
        log.warning("dbus-fast not installed; Bluetooth disabled")
        return BluetoothBackend(on_change)
    b = BluezBackend(on_change, adapter)
    try:
        await b.start()
        return b
    except Exception as e:  # noqa: BLE001
        log.warning("BlueZ unavailable: %s", e)
        return BluetoothBackend(on_change)
