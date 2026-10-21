"""Unit tests for the on-prem server sizing calculator (tools/capacity_sizing.py).

Pure arithmetic, so these run without the lab -- they back the pilot criterion
"session host CPU and RAM per user measured, to size production" (L-3) by
checking the measurement -> server-spec math, and the reverse capacity math.
"""
import importlib.util
from pathlib import Path

import pytest

SPEC = Path(__file__).resolve().parent.parent / "tools" / "capacity_sizing.py"
spec = importlib.util.spec_from_file_location("capacity_sizing", SPEC)
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)


def test_infra_totals_sum_the_table():
    vcpu, ram = cs.infra_totals()
    assert vcpu == sum(c for c, _ in cs.INFRA_VMS.values())
    assert ram == sum(r for _, r in cs.INFRA_VMS.values())


def test_ceil_to_rounds_up_to_a_buyable_size():
    assert cs.ceil_to(17, 4) == 20
    assert cs.ceil_to(432, 32) == 448
    assert cs.ceil_to(64, 32) == 64   # already a multiple


def test_from_usage_takes_the_worst_row(tmp_path):
    cap = tmp_path / "pilot.txt"
    cap.write_text(
        "# 2026-11-02T09:00:00+00:00\n"
        "host    users  MB/user  CPU%/user   host RAM used  load1 cores\n"
        "sh-01      40     1700       15.0     70000/128000   2.1    16\n"
        "sh-02      38     1950       24.0     72000/128000   3.4    16\n"
    )
    ram_mb, cpu_pct = cs.read_usage(str(cap))
    assert ram_mb == 1950   # the busier of the two rows, not the average
    assert cpu_pct == 24.0


def test_from_usage_rejects_an_empty_capture(tmp_path):
    cap = tmp_path / "empty.txt"
    cap.write_text("# just a header\nhost users MB/user CPU%/user\n")
    with pytest.raises(SystemExit):
        cs.read_usage(str(cap))


def _args(**over):
    """Defaults matching the argparse defaults, overridable per test."""
    base = dict(agents=None, server_cores=None, server_ram_gb=None,
                ram_mb=1800, cpu_pct=18, from_usage=None,
                concurrency=0.9, ram_headroom=0.2, cpu_overcommit=3.0,
                max_users_per_host=50, host_ram_gb=64,
                redundant_host=True, two_servers=False,
                host_reserve_cores=2, host_reserve_ram_gb=8, measured=True)
    base.update(over)
    return cs.argparse.Namespace(**base)


def test_forward_sizing_is_internally_consistent(capsys):
    cs.forward(_args(agents=200))
    out = capsys.readouterr().out
    # 200 agents * 90% = 180 concurrent; 180 * 1800MB = ~316 GB pool.
    assert "180 logged in at once" in out
    assert "316 GB" in out
    # 316 GB / (64 GB * 80% usable) = 7 active hosts (RAM-bound, not the
    # 50-user cap), +1 for failover.
    assert "7 active + 1 for failover" in out
    # Devices are reused, not replaced -- the whole point of the request.
    assert "NOT replaced" in out


def test_reverse_capacity_is_bounded_by_the_tighter_resource(capsys):
    # Tiny RAM, plenty of cores -> RAM-bound.
    cs.reverse(_args(server_cores=64, server_ram_gb=64))
    out = capsys.readouterr().out
    assert "bound by RAM" in out


def test_bigger_server_fits_more_agents(capsys):
    cs.reverse(_args(server_cores=32, server_ram_gb=256))
    small = capsys.readouterr().out
    cs.reverse(_args(server_cores=64, server_ram_gb=512))
    big = capsys.readouterr().out

    def agents(text):
        line = [ln for ln in text.splitlines() if "total agents" in ln][0]
        return int(line.split()[2])   # "total agents  <N>  (at ...)"

    assert agents(big) > agents(small)
