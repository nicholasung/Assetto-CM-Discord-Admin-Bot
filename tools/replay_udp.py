#!/usr/bin/env python3
"""Replay a fake AC session against a running acbot — no game needed.

Sends scripted ACSP packets to the bot's UDP plugin listener so you can watch
the status embed update and laps appear in /lb, e.g.:

    python tools/replay_udp.py --target 127.0.0.1:12000

Run it from the repo root (it imports acbot).
"""

from __future__ import annotations

import argparse
import random
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from acbot.ac import udp  # noqa: E402

DRIVERS = [
    ("Ayrton Sim", "76561198000000101", "ks_mazda_mx5_cup", "red"),
    ("Michael Schuemulator", "76561198000000102", "ks_mazda_mx5_cup", "blue"),
    ("Test Driver", "76561198000000103", "abarth500", "stripes"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="127.0.0.1:12000",
                        help="bot UDP plugin listen address (host:port)")
    parser.add_argument("--laps", type=int, default=3, help="laps per driver")
    parser.add_argument("--fast", action="store_true", help="no sleeps")
    args = parser.parse_args()

    host, port = args.target.rsplit(":", 1)
    target = (host, int(port))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(payload: bytes, label: str) -> None:
        sock.sendto(payload, target)
        print(f"  -> {label}")
        if not args.fast:
            time.sleep(0.6)

    session = udp.SessionInfo(
        protocol_version=4, server_name="Replay Server",
        track="ks_brands_hatch", track_config="gp", name="Practice",
        session_type=1, ambient_temp=24, road_temp=31,
        weather_graphics="3_clear",
    )

    print(f"Replaying a fake session to {target[0]}:{target[1]}")
    send(udp.build_version(4), "VERSION")
    send(udp.build_session_info(session, packet_id=udp.ACSP_NEW_SESSION), "NEW_SESSION")

    events = []
    for car_id, (name, guid, model, skin) in enumerate(DRIVERS):
        ev = udp.ConnectionEvent(driver_name=name, driver_guid=guid,
                                 car_id=car_id, car_model=model, car_skin=skin)
        events.append(ev)
        send(udp.build_connection(ev), f"JOIN {name} ({model})")
        send(udp.build_client_loaded(car_id), f"LOADED {name}")

    base = {0: 92000, 1: 93000, 2: 101000}
    for lap_no in range(args.laps):
        for car_id, (name, *_rest) in enumerate(DRIVERS):
            laptime = base[car_id] + random.randint(-1500, 2500)
            cuts = random.choice([0, 0, 0, 1])
            send(udp.build_lap_completed(
                udp.LapCompleted(car_id=car_id, laptime_ms=laptime, cuts=cuts)),
                f"LAP {name} {laptime/1000:.3f}s cuts={cuts} (lap {lap_no + 1})")

    for ev in events:
        send(udp.build_connection(ev, packet_id=udp.ACSP_CONNECTION_CLOSED),
             f"LEAVE {ev.driver_name}")
    send(udp.build_end_session(""), "END_SESSION")
    print("Done. Check /lb recent and the status message.")


if __name__ == "__main__":
    main()
