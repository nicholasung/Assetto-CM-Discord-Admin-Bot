"""ACSP — the AC dedicated server UDP plugin protocol.

The server pushes events (connections, laps, sessions, collisions) to
UDP_PLUGIN_ADDRESS and accepts requests/admin packets on
UDP_PLUGIN_LOCAL_PORT. Reference: Kunos acplugins protocol, version 4.

Parsing is defensive: a malformed/unknown packet logs and is dropped, it can
never take down the listener. Strings come in two flavors: single-byte
(length + UTF-8 bytes) and wide (length + UTF-32LE chars).
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# server -> plugin
ACSP_NEW_SESSION = 50
ACSP_NEW_CONNECTION = 51
ACSP_CONNECTION_CLOSED = 52
ACSP_CAR_UPDATE = 53
ACSP_CAR_INFO = 54
ACSP_END_SESSION = 55
ACSP_VERSION = 56
ACSP_CHAT = 57
ACSP_CLIENT_LOADED = 58
ACSP_SESSION_INFO = 59
ACSP_ERROR = 60
ACSP_LAP_COMPLETED = 73
ACSP_CLIENT_EVENT = 130

ACSP_CE_COLLISION_WITH_CAR = 10
ACSP_CE_COLLISION_WITH_ENV = 11

# plugin -> server
ACSP_REALTIMEPOS_INTERVAL = 200
ACSP_GET_CAR_INFO = 201
ACSP_SEND_CHAT = 202
ACSP_BROADCAST_CHAT = 203
ACSP_GET_SESSION_INFO = 204
ACSP_SET_SESSION_INFO = 205
ACSP_KICK_USER = 206
ACSP_NEXT_SESSION = 207
ACSP_RESTART_SESSION = 208
ACSP_ADMIN_COMMAND = 209

SESSION_TYPES = {0: "booking", 1: "practice", 2: "qualify", 3: "race"}


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def u8(self) -> int:
        v = self.data[self.pos]
        self.pos += 1
        return v

    def u16(self) -> int:
        (v,) = struct.unpack_from("<H", self.data, self.pos)
        self.pos += 2
        return v

    def i32(self) -> int:
        (v,) = struct.unpack_from("<i", self.data, self.pos)
        self.pos += 4
        return v

    def u32(self) -> int:
        (v,) = struct.unpack_from("<I", self.data, self.pos)
        self.pos += 4
        return v

    def f32(self) -> float:
        (v,) = struct.unpack_from("<f", self.data, self.pos)
        self.pos += 4
        return v

    def vec3(self) -> tuple[float, float, float]:
        return (self.f32(), self.f32(), self.f32())

    def string(self) -> str:
        n = self.u8()
        raw = self.data[self.pos:self.pos + n]
        self.pos += n
        return raw.decode("utf-8", errors="replace")

    def string_w(self) -> str:
        n = self.u8()
        raw = self.data[self.pos:self.pos + n * 4]
        self.pos += n * 4
        return raw.decode("utf-32-le", errors="replace")

    @property
    def remaining(self) -> int:
        return len(self.data) - self.pos


def _write_string_w(text: str) -> bytes:
    text = text[:255]
    return bytes([len(text)]) + text.encode("utf-32-le", errors="replace")


# --------------------------------------------------------------------------
# Event dataclasses

@dataclass
class SessionInfo:
    protocol_version: int = 0
    session_index: int = 0
    current_session_index: int = 0
    session_count: int = 0
    server_name: str = ""
    track: str = ""
    track_config: str = ""
    name: str = ""
    session_type: int = 0
    time_mins: int = 0
    laps: int = 0
    wait_time: int = 0
    ambient_temp: int = 0
    road_temp: int = 0
    weather_graphics: str = ""
    elapsed_ms: int = 0

    @property
    def type_name(self) -> str:
        return SESSION_TYPES.get(self.session_type, f"type{self.session_type}")


@dataclass
class ConnectionEvent:
    driver_name: str
    driver_guid: str
    car_id: int
    car_model: str
    car_skin: str


@dataclass
class CarInfo:
    car_id: int
    is_connected: bool
    car_model: str
    car_skin: str
    driver_name: str
    driver_team: str
    driver_guid: str


@dataclass
class CarUpdate:
    car_id: int
    pos: tuple[float, float, float]
    velocity: tuple[float, float, float]
    gear: int
    engine_rpm: int
    normalized_spline_pos: float


@dataclass
class LeaderboardLine:
    car_id: int
    laptime_ms: int
    laps: int
    completed_last_lap: bool | None = None


@dataclass
class LapCompleted:
    car_id: int
    laptime_ms: int
    cuts: int
    grip_level: float | None = None
    leaderboard: list[LeaderboardLine] = field(default_factory=list)


@dataclass
class ClientEvent:
    event_type: int
    car_id: int
    other_car_id: int | None
    impact_speed: float
    world_pos: tuple[float, float, float]
    rel_pos: tuple[float, float, float]


@dataclass
class EndSession:
    report_file: str


@dataclass
class Chat:
    car_id: int
    message: str


@dataclass
class ClientLoaded:
    car_id: int


@dataclass
class ProtocolVersion:
    version: int


@dataclass
class ProtocolError:
    message: str


Event = (
    SessionInfo | ConnectionEvent | CarInfo | CarUpdate | LapCompleted
    | ClientEvent | EndSession | Chat | ClientLoaded | ProtocolVersion | ProtocolError
)


# --------------------------------------------------------------------------
# Parser

def parse_packet(data: bytes) -> tuple[int, Event] | None:
    """Returns (packet_id, event) or None for unknown/bad packets."""
    if not data:
        return None
    packet_id = data[0]
    r = _Reader(data)
    r.pos = 1
    try:
        if packet_id in (ACSP_NEW_SESSION, ACSP_SESSION_INFO):
            return packet_id, SessionInfo(
                protocol_version=r.u8(),
                session_index=r.u8(),
                current_session_index=r.u8(),
                session_count=r.u8(),
                server_name=r.string_w(),
                track=r.string(),
                track_config=r.string(),
                name=r.string(),
                session_type=r.u8(),
                time_mins=r.u16(),
                laps=r.u16(),
                wait_time=r.u16(),
                ambient_temp=r.u8(),
                road_temp=r.u8(),
                weather_graphics=r.string(),
                elapsed_ms=r.i32(),
            )
        if packet_id in (ACSP_NEW_CONNECTION, ACSP_CONNECTION_CLOSED):
            return packet_id, ConnectionEvent(
                driver_name=r.string_w(),
                driver_guid=r.string_w(),
                car_id=r.u8(),
                car_model=r.string(),
                car_skin=r.string(),
            )
        if packet_id == ACSP_CAR_INFO:
            return packet_id, CarInfo(
                car_id=r.u8(),
                is_connected=bool(r.u8()),
                car_model=r.string_w(),
                car_skin=r.string_w(),
                driver_name=r.string_w(),
                driver_team=r.string_w(),
                driver_guid=r.string_w(),
            )
        if packet_id == ACSP_CAR_UPDATE:
            return packet_id, CarUpdate(
                car_id=r.u8(),
                pos=r.vec3(),
                velocity=r.vec3(),
                gear=r.u8(),
                engine_rpm=r.u16(),
                normalized_spline_pos=r.f32(),
            )
        if packet_id == ACSP_LAP_COMPLETED:
            ev = LapCompleted(car_id=r.u8(), laptime_ms=r.u32(), cuts=r.u8())
            count = r.u8()
            # Trailing grip float; per-line size differs across protocol
            # versions (7 bytes, or 8 with a completed-last-lap flag).
            body = r.remaining - 4
            if count and body >= 0 and body % count == 0 and body // count in (7, 8):
                per = body // count
                for _ in range(count):
                    line = LeaderboardLine(
                        car_id=r.u8(), laptime_ms=r.u32(), laps=r.u16()
                    )
                    if per == 8:
                        line.completed_last_lap = bool(r.u8())
                    ev.leaderboard.append(line)
                ev.grip_level = r.f32()
            elif r.remaining == 4:
                ev.grip_level = r.f32()
            return packet_id, ev
        if packet_id == ACSP_CLIENT_EVENT:
            ev_type = r.u8()
            car_id = r.u8()
            other = r.u8() if ev_type == ACSP_CE_COLLISION_WITH_CAR else None
            return packet_id, ClientEvent(
                event_type=ev_type,
                car_id=car_id,
                other_car_id=other,
                impact_speed=r.f32(),
                world_pos=r.vec3(),
                rel_pos=r.vec3(),
            )
        if packet_id == ACSP_END_SESSION:
            return packet_id, EndSession(report_file=r.string_w())
        if packet_id == ACSP_CHAT:
            return packet_id, Chat(car_id=r.u8(), message=r.string_w())
        if packet_id == ACSP_CLIENT_LOADED:
            return packet_id, ClientLoaded(car_id=r.u8())
        if packet_id == ACSP_VERSION:
            return packet_id, ProtocolVersion(version=r.u8())
        if packet_id == ACSP_ERROR:
            return packet_id, ProtocolError(message=r.string_w())
    except (IndexError, struct.error) as e:
        log.warning("bad ACSP packet id=%d len=%d: %s", packet_id, len(data), e)
        return None
    log.debug("unknown ACSP packet id=%d len=%d", packet_id, len(data))
    return None


# --------------------------------------------------------------------------
# Request builders (plugin -> server)

def build_get_session_info(session_index: int = -1) -> bytes:
    return struct.pack("<Bh", ACSP_GET_SESSION_INFO, session_index)


def build_get_car_info(car_id: int) -> bytes:
    return struct.pack("<BB", ACSP_GET_CAR_INFO, car_id)


def build_realtimepos_interval(ms: int) -> bytes:
    return struct.pack("<BH", ACSP_REALTIMEPOS_INTERVAL, ms)


def build_broadcast_chat(message: str) -> bytes:
    return bytes([ACSP_BROADCAST_CHAT]) + _write_string_w(message)


def build_send_chat(car_id: int, message: str) -> bytes:
    return bytes([ACSP_SEND_CHAT, car_id]) + _write_string_w(message)


def build_admin_command(command: str) -> bytes:
    return bytes([ACSP_ADMIN_COMMAND]) + _write_string_w(command)


def build_kick(car_id: int) -> bytes:
    return struct.pack("<BB", ACSP_KICK_USER, car_id)


def build_next_session() -> bytes:
    return bytes([ACSP_NEXT_SESSION])


def build_restart_session() -> bytes:
    return bytes([ACSP_RESTART_SESSION])


# --------------------------------------------------------------------------
# Serializers for the events the *server* sends — used by tests and
# tools/replay_udp.py to fabricate server traffic.

def _write_string(text: str) -> bytes:
    raw = text.encode("utf-8")[:255]
    return bytes([len(raw)]) + raw


def build_session_info(info: SessionInfo, packet_id: int = ACSP_SESSION_INFO) -> bytes:
    return (
        bytes([packet_id, info.protocol_version, info.session_index,
               info.current_session_index, info.session_count])
        + _write_string_w(info.server_name)
        + _write_string(info.track)
        + _write_string(info.track_config)
        + _write_string(info.name)
        + bytes([info.session_type])
        + struct.pack("<HHH", info.time_mins, info.laps, info.wait_time)
        + bytes([info.ambient_temp, info.road_temp])
        + _write_string(info.weather_graphics)
        + struct.pack("<i", info.elapsed_ms)
    )


def build_connection(ev: ConnectionEvent, packet_id: int = ACSP_NEW_CONNECTION) -> bytes:
    return (
        bytes([packet_id])
        + _write_string_w(ev.driver_name)
        + _write_string_w(ev.driver_guid)
        + bytes([ev.car_id])
        + _write_string(ev.car_model)
        + _write_string(ev.car_skin)
    )


def build_car_info(ev: CarInfo) -> bytes:
    return (
        bytes([ACSP_CAR_INFO, ev.car_id, 1 if ev.is_connected else 0])
        + _write_string_w(ev.car_model)
        + _write_string_w(ev.car_skin)
        + _write_string_w(ev.driver_name)
        + _write_string_w(ev.driver_team)
        + _write_string_w(ev.driver_guid)
    )


def build_lap_completed(ev: LapCompleted, with_completed_flag: bool = True) -> bytes:
    out = struct.pack("<BBIB", ACSP_LAP_COMPLETED, ev.car_id, ev.laptime_ms, ev.cuts)
    out += bytes([len(ev.leaderboard)])
    for line in ev.leaderboard:
        out += struct.pack("<BIH", line.car_id, line.laptime_ms, line.laps)
        if with_completed_flag:
            out += bytes([1 if line.completed_last_lap else 0])
    out += struct.pack("<f", ev.grip_level if ev.grip_level is not None else 1.0)
    return out


def build_end_session(report_file: str) -> bytes:
    return bytes([ACSP_END_SESSION]) + _write_string_w(report_file)


def build_client_loaded(car_id: int) -> bytes:
    return bytes([ACSP_CLIENT_LOADED, car_id])


def build_version(version: int = 4) -> bytes:
    return bytes([ACSP_VERSION, version])


def build_chat(car_id: int, message: str) -> bytes:
    return bytes([ACSP_CHAT, car_id]) + _write_string_w(message)


def build_client_event_env(car_id: int, speed: float) -> bytes:
    return (
        bytes([ACSP_CLIENT_EVENT, ACSP_CE_COLLISION_WITH_ENV, car_id])
        + struct.pack("<7f", speed, 0, 0, 0, 0, 0, 0)
    )


# --------------------------------------------------------------------------
# Roster / listener

@dataclass
class Driver:
    car_id: int
    name: str
    guid: str
    model: str
    skin: str
    connected: bool = True
    joined_at: float = field(default_factory=time.time)


class AcspListener(asyncio.DatagramProtocol):
    """Binds the plugin address, tracks roster + session, republishes on the bus."""

    PENDING_LAP_TTL = 10.0

    def __init__(self, bus, listen_host: str, listen_port: int, server_port: int,
                 entry_count_hint=None):
        self.bus = bus
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.server_addr = ("127.0.0.1", server_port)
        self.entry_count_hint = entry_count_hint or (lambda: 24)
        self.transport: asyncio.DatagramTransport | None = None
        self.roster: dict[int, Driver] = {}
        self.session: SessionInfo | None = None
        self.protocol_version: int | None = None
        self._pending_laps: list[tuple[float, LapCompleted]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: set[asyncio.Task] = set()

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self._loop.create_datagram_endpoint(
            lambda: self, local_addr=(self.listen_host, self.listen_port)
        )
        if self.listen_port == 0 and self.transport:  # tests bind an ephemeral port
            self.listen_port = self.transport.get_extra_info("sockname")[1]
        log.info("ACSP listener on %s:%d -> server 127.0.0.1:%d",
                 self.listen_host, self.listen_port, self.server_addr[1])

    def close(self) -> None:
        if self.transport:
            self.transport.close()
            self.transport = None

    def reset(self) -> None:
        """Called on server (re)start: forget roster/session."""
        self.roster.clear()
        self.session = None
        self.protocol_version = None
        self._pending_laps.clear()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def error_received(self, exc: Exception) -> None:
        # Windows raises WSAECONNRESET here when the server isn't listening.
        log.debug("ACSP socket error (server down?): %s", exc)

    def send(self, payload: bytes) -> None:
        if self.transport:
            self.transport.sendto(payload, self.server_addr)

    # -- convenience requests ------------------------------------------------

    def request_session_info(self) -> None:
        self.send(build_get_session_info(-1))

    def request_car_info(self, car_id: int) -> None:
        self.send(build_get_car_info(car_id))

    def request_all_car_info(self) -> None:
        try:
            n = max(1, min(64, int(self.entry_count_hint())))
        except Exception:
            n = 24
        for car_id in range(n):
            self.send(build_get_car_info(car_id))

    def broadcast_chat(self, message: str) -> None:
        self.send(build_broadcast_chat(message))

    # -- inbound -------------------------------------------------------------

    def datagram_received(self, data: bytes, addr) -> None:
        parsed = parse_packet(data)
        if parsed is None or self._loop is None:
            return
        packet_id, event = parsed
        # Keep a strong reference so in-flight handlers can't be GC'd.
        task = self._loop.create_task(self._handle(packet_id, event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(self, packet_id: int, event: Event) -> None:
        if isinstance(event, ProtocolVersion):
            self.protocol_version = event.version
            self.request_session_info()
            self.request_all_car_info()
            return

        if isinstance(event, SessionInfo):
            is_new = packet_id == ACSP_NEW_SESSION
            self.session = event
            if is_new:
                self.request_all_car_info()
            await self.bus.emit("session_info", session=event, is_new=is_new)
            return

        if isinstance(event, ConnectionEvent):
            if packet_id == ACSP_NEW_CONNECTION:
                driver = Driver(
                    car_id=event.car_id, name=event.driver_name, guid=event.driver_guid,
                    model=event.car_model, skin=event.car_skin,
                )
                self.roster[event.car_id] = driver
                await self.bus.emit("driver_joined", driver=driver)
            else:
                driver = self.roster.pop(event.car_id, None) or Driver(
                    car_id=event.car_id, name=event.driver_name, guid=event.driver_guid,
                    model=event.car_model, skin=event.car_skin, connected=False,
                )
                driver.connected = False
                await self.bus.emit("driver_left", driver=driver)
            return

        if isinstance(event, CarInfo):
            if event.is_connected and event.driver_guid:
                self.roster[event.car_id] = Driver(
                    car_id=event.car_id, name=event.driver_name, guid=event.driver_guid,
                    model=event.car_model, skin=event.car_skin,
                )
                await self._flush_pending_laps()
            else:
                self.roster.pop(event.car_id, None)
            return

        if isinstance(event, LapCompleted):
            driver = self.roster.get(event.car_id)
            if driver is None:
                # Bot may have (re)started mid-session: ask who this is and
                # park the lap until the CAR_INFO answer lands.
                self._pending_laps.append((time.time(), event))
                self.request_car_info(event.car_id)
                return
            await self._emit_lap(event, driver)
            return

        if isinstance(event, ClientEvent):
            await self.bus.emit(
                "collision",
                car_id=event.car_id,
                other_car_id=event.other_car_id,
                impact_speed=event.impact_speed,
                driver=self.roster.get(event.car_id),
            )
            return

        if isinstance(event, EndSession):
            await self.bus.emit("end_session", report_file=event.report_file)
            return

        if isinstance(event, Chat):
            await self.bus.emit("chat", car_id=event.car_id, message=event.message,
                                driver=self.roster.get(event.car_id))
            return

        if isinstance(event, ClientLoaded):
            await self.bus.emit("client_loaded", car_id=event.car_id,
                                driver=self.roster.get(event.car_id))
            return

        if isinstance(event, ProtocolError):
            log.debug("ACSP error from server: %s", event.message)

    async def _emit_lap(self, event: LapCompleted, driver: Driver) -> None:
        await self.bus.emit(
            "lap_completed",
            driver=driver,
            laptime_ms=event.laptime_ms,
            cuts=event.cuts,
            grip=event.grip_level,
            session=self.session,
        )

    async def _flush_pending_laps(self) -> None:
        if not self._pending_laps:
            return
        now = time.time()
        still_pending: list[tuple[float, LapCompleted]] = []
        for ts, lap in self._pending_laps:
            driver = self.roster.get(lap.car_id)
            if driver is not None:
                await self._emit_lap(lap, driver)
            elif now - ts < self.PENDING_LAP_TTL:
                still_pending.append((ts, lap))
            else:
                log.warning("dropping lap for unknown car %d (no CAR_INFO)", lap.car_id)
        self._pending_laps = still_pending
