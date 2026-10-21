#!/usr/bin/env python3
"""Snapshot of CPU and RAM per logged-in user on every session host, for the
pilot criterion "session host CPU and RAM per user measured, to size production".

Run it a few times per shift during the pilot (or from cron) and keep the output:
  python3 tools/sessionhost_usage.py                       # simulated lab
  LAB_TARGETS=tests/targets/lab.yml python3 tools/sessionhost_usage.py
"""
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from conftest import CFG, Lab  # noqa: E402

MIN_UID = 1000


def main():
    lab = Lab(CFG)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"# {now}")
    print(f"{'host':<8}{'users':>6}{'MB/user':>9}{'CPU%/user':>11}{'host RAM used':>15}{'load1':>7}{'cores':>6}")
    for name in CFG["session_hosts"]:
        h = lab.host(name)
        ps = h.check_output("ps -eo uid=,rss=,pcpu=")
        rss, cpu = defaultdict(int), defaultdict(float)
        for line in ps.splitlines():
            uid, kb, pc = line.split()
            if int(uid) >= MIN_UID and int(uid) != 65534:
                rss[uid] += int(kb)
                cpu[uid] += float(pc)
        users = len(rss)
        mem = h.check_output("free -m | awk '/^Mem:/ {print $3\"/\"$2\" MB\"}'")
        load = h.check_output("cut -d' ' -f1 /proc/loadavg")
        cores = h.check_output("nproc")
        per_mb = sum(rss.values()) / 1024 / users if users else 0
        per_cpu = sum(cpu.values()) / users if users else 0
        print(f"{name:<8}{users:>6}{per_mb:>9.0f}{per_cpu:>11.1f}{mem:>15}{load:>7}{cores:>6}")


if __name__ == "__main__":
    main()
