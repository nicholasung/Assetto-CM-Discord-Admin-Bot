import asyncio

import pytest

from acbot.ac import udp
from acbot.events import EventBus


def test_session_info_round_trip():
    info = udp.SessionInfo(
        protocol_version=4, session_index=1, current_session_index=1, session_count=3,
        server_name="Tëst Server", track="ks_brands_hatch", track_config="gp",
        name="Quick Race", session_type=3, time_mins=0, laps=10, wait_time=60,
        ambient_temp=26, road_temp=32, weather_graphics="3_clear", elapsed_ms=123456,
    )
    packet_id, parsed = udp.parse_packet(udp.build_session_info(info))
    assert packet_id == udp.ACSP_SESSION_INFO
    assert parsed == info
    assert parsed.type_name == "race"


def test_connection_round_trip_wide_strings():
    ev = udp.ConnectionEvent(
        driver_name="Jörg Müller 日本", driver_guid="76561198000000001",
        car_id=7, car_model="ks_mazda_mx5_cup", car_skin="red",
    )
    _, parsed = udp.parse_packet(udp.build_connection(ev))
    assert parsed == ev
    _, closed = udp.parse_packet(
        udp.build_connection(ev, packet_id=udp.ACSP_CONNECTION_CLOSED))
    assert closed == ev


def test_car_info_round_trip():
    ev = udp.CarInfo(
        car_id=3, is_connected=True, car_model="abarth500", car_skin="stripes",
        driver_name="Alice", driver_team="", driver_guid="76561198000000002",
    )
    _, parsed = udp.parse_packet(udp.build_car_info(ev))
    assert parsed == ev


@pytest.mark.parametrize("with_flag", [True, False])
def test_lap_completed_both_leaderboard_layouts(with_flag: bool):
    ev = udp.LapCompleted(
        car_id=0, laptime_ms=92123, cuts=0, grip_level=0.98,
        leaderboard=[
            udp.LeaderboardLine(car_id=0, laptime_ms=92123, laps=3,
                                completed_last_lap=with_flag or None),
            udp.LeaderboardLine(car_id=1, laptime_ms=93999, laps=3,
                                completed_last_lap=False if with_flag else None),
        ],
    )
    _, parsed = udp.parse_packet(udp.build_lap_completed(ev, with_completed_flag=with_flag))
    assert parsed.car_id == 0
    assert parsed.laptime_ms == 92123
    assert parsed.cuts == 0
    assert parsed.grip_level == pytest.approx(0.98)
    assert [line.car_id for line in parsed.leaderboard] == [0, 1]
    assert [line.laptime_ms for line in parsed.leaderboard] == [92123, 93999]


def test_end_session_chat_loaded_version_error():
    _, ev = udp.parse_packet(udp.build_end_session("results/2026_7_5_RACE.json"))
    assert ev.report_file == "results/2026_7_5_RACE.json"
    _, chat = udp.parse_packet(udp.build_chat(2, "hello 🏁"))
    assert (chat.car_id, chat.message) == (2, "hello 🏁")
    _, loaded = udp.parse_packet(udp.build_client_loaded(5))
    assert loaded.car_id == 5
    _, ver = udp.parse_packet(udp.build_version(4))
    assert ver.version == 4


def test_client_event_env_collision():
    _, ev = udp.parse_packet(udp.build_client_event_env(4, 55.5))
    assert ev.event_type == udp.ACSP_CE_COLLISION_WITH_ENV
    assert ev.car_id == 4
    assert ev.other_car_id is None
    assert ev.impact_speed == pytest.approx(55.5)


def test_malformed_packets_do_not_raise():
    assert udp.parse_packet(b"") is None
    assert udp.parse_packet(bytes([99, 1, 2, 3])) is None  # unknown id
    # Truncated session info: header says strings follow, but data ends.
    assert udp.parse_packet(bytes([udp.ACSP_SESSION_INFO, 4, 0])) is None


def test_request_builders_shape():
    assert udp.build_get_session_info(-1)[0] == udp.ACSP_GET_SESSION_INFO
    assert udp.build_get_car_info(3) == bytes([udp.ACSP_GET_CAR_INFO, 3])
    assert udp.build_admin_command("/next_session")[0] == udp.ACSP_ADMIN_COMMAND
    assert udp.build_broadcast_chat("hi")[0] == udp.ACSP_BROADCAST_CHAT
    assert udp.build_kick(9) == bytes([udp.ACSP_KICK_USER, 9])


class Recorder:
    def __init__(self, bus: EventBus, names: list[str]):
        self.events: list[tuple[str, dict]] = []
        for name in names:
            bus.subscribe(name, self._make(name))

    def _make(self, name):
        async def handler(**kw):
            self.events.append((name, kw))
        return handler

    def named(self, name):
        return [kw for n, kw in self.events if n == name]


async def test_listener_roster_and_pending_laps():
    bus = EventBus()
    rec = Recorder(bus, ["driver_joined", "driver_left", "lap_completed", "session_info"])
    listener = udp.AcspListener(bus, "127.0.0.1", 0, 11000)
    listener._loop = asyncio.get_running_loop()

    session = udp.SessionInfo(track="ks_brands_hatch", track_config="gp",
                              session_type=1, ambient_temp=20, road_temp=25)
    await listener._handle(udp.ACSP_NEW_SESSION, session)
    assert listener.session is session
    assert rec.named("session_info")[0]["is_new"] is True

    # A lap for an unknown car goes pending, then flushes once CAR_INFO lands.
    lap = udp.LapCompleted(car_id=0, laptime_ms=90000, cuts=0)
    await listener._handle(udp.ACSP_LAP_COMPLETED, lap)
    assert rec.named("lap_completed") == []
    assert len(listener._pending_laps) == 1

    info = udp.CarInfo(car_id=0, is_connected=True, car_model="abarth500",
                       car_skin="stripes", driver_name="Alice", driver_team="",
                       driver_guid="76561198000000002")
    await listener._handle(udp.ACSP_CAR_INFO, info)
    laps = rec.named("lap_completed")
    assert len(laps) == 1
    assert laps[0]["driver"].guid == "76561198000000002"
    assert laps[0]["laptime_ms"] == 90000
    assert listener._pending_laps == []

    # Normal join/leave flow.
    join = udp.ConnectionEvent(driver_name="Bob", driver_guid="76561198000000003",
                               car_id=1, car_model="ks_mazda_mx5_cup", car_skin="red")
    await listener._handle(udp.ACSP_NEW_CONNECTION, join)
    assert listener.roster[1].name == "Bob"
    lap2 = udp.LapCompleted(car_id=1, laptime_ms=95000, cuts=2)
    await listener._handle(udp.ACSP_LAP_COMPLETED, lap2)
    assert rec.named("lap_completed")[-1]["cuts"] == 2

    await listener._handle(udp.ACSP_CONNECTION_CLOSED, join)
    assert 1 not in listener.roster
    assert rec.named("driver_left")[0]["driver"].guid == "76561198000000003"
