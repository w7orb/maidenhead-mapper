"""Replay WSJT-X ALL.TXT logs as live UDP packets, for testing without a radio.

Sends Heartbeat, Status (dial frequency/mode taken from each line) and Decode
packets, in the same order and spacing as the original log, sped up by
--speed. Only received ('Rx') lines are sent, as WSJT-X does.

    python tools/replay_alltxt.py path/to/ALL.TXT [more files] --speed 30
    python tools/replay_alltxt.py ALL.TXT --address 127.0.0.1           # unicast
    python tools/replay_alltxt.py ALL.TXT --qso K1ABC:FN42              # then log a QSO

Standard library only; uses the plugin's encoder.
"""

import argparse
import datetime as dt
import os
import socket
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from maidenhead_mapper.core import wsjtx_udp as w  # noqa: E402


def read_lines(paths):
    rows = []
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                p = line.split(None, 7)
                if len(p) < 8 or p[2] != "Rx":
                    continue
                try:
                    t = dt.datetime.strptime(p[0], "%y%m%d_%H%M%S").replace(tzinfo=dt.timezone.utc)
                    rows.append((t, float(p[1]), p[3], int(p[4]), float(p[5]), int(p[6]), p[7].strip()))
                except ValueError:
                    continue
    rows.sort(key=lambda r: r[0])
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--address", default="239.255.0.1", help="multicast group or unicast IP (default 239.255.0.1)")
    ap.add_argument("--port", type=int, default=2237)
    ap.add_argument("--speed", type=float, default=30.0, help="time compression; 0 = as fast as possible")
    ap.add_argument("--limit", type=int, default=0, help="stop after N decodes")
    ap.add_argument("--id", default="WSJT-X-replay")
    ap.add_argument("--de-call", default="N0CALL", help="callsign the fake WSJT-X reports as its own")
    ap.add_argument("--de-grid", default="AA00", help="grid the fake WSJT-X reports as its own")
    ap.add_argument("--qso", action="append", default=[], help="CALL:GRID to log as a QSO at the end")
    args = ap.parse_args()

    rows = read_lines(args.files)
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        sys.exit("no Rx lines found")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    dest = (args.address, args.port)

    def send(data):
        sock.sendto(data, dest)

    send(w.encode_heartbeat(args.id, version="replay"))
    status = None
    start_log, start_wall = rows[0][0], time.monotonic()
    for i, (t, mhz, mode, snr, dtv, df, msg) in enumerate(rows):
        if args.speed > 0:
            due = (t - start_log).total_seconds() / args.speed
            delay = due - (time.monotonic() - start_wall)
            if delay > 0:
                time.sleep(delay)
        if status != (mhz, mode):
            status = (mhz, mode)
            send(w.encode_status(args.id, int(round(mhz * 1e6)), mode, de_call=args.de_call, de_grid=args.de_grid))
        ms = ((t.hour * 60 + t.minute) * 60 + t.second) * 1000
        send(w.encode_decode(msg, snr=snr, time_ms=ms, client_id=args.id, dt=dtv, df=df,
                             mode="~" if mode == "FT8" else "+"))
        if i % 100 == 0:
            print(f"{i}/{len(rows)} {t:%H:%M:%S} {msg}", flush=True)

    for q in args.qso:
        call, _, grid = q.partition(":")
        now = dt.datetime.now(dt.timezone.utc)
        send(w.encode_qso_logged(call, grid, int(round(rows[-1][1] * 1e6)), rows[-1][2], time_off=now,
                                 time_on=now, client_id=args.id, my_call=args.de_call, my_grid=args.de_grid))
        print("logged QSO", call, grid)
    send(w.encode_heartbeat(args.id, version="replay"))
    print(f"sent {len(rows)} decodes to {args.address}:{args.port}")


if __name__ == "__main__":
    main()
