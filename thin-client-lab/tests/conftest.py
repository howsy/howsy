"""Shared fixtures. The same tests run against the simulated lab (default) or the
real lab: LAB_TARGETS=tests/targets/lab.yml pytest tests"""
import os
import re
from pathlib import Path

import pytest
import testinfra
import yaml

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"


def _load_dotenv():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _expand(value):
    """${VAR} and ${VAR:-default} from the environment."""
    if isinstance(value, str):
        return re.sub(
            r"\$\{(\w+)(?::-([^}]*))?\}",
            lambda m: os.environ.get(m.group(1)) or (m.group(2) or ""),
            value,
        )
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def load_config():
    _load_dotenv()
    path = Path(os.environ.get("LAB_TARGETS", TESTS / "targets" / "sim.yml"))
    if not path.is_absolute():
        path = ROOT / path
    return _expand(yaml.safe_load(path.read_text()))


CFG = load_config()


class Lab:
    """Access to lab machines by their logical name (sh-01, ep-home-01, ...)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._hosts = {}

    @property
    def sim(self):
        return self.cfg["mode"] == "sim"

    def host(self, name):
        if name not in self._hosts:
            spec = self.cfg["hosts"][name]
            self._hosts[name] = testinfra.get_host(spec)
        return self._hosts[name]

    def fmt(self, template, host):
        return template.format(host=host, vmid=self.cfg.get("vmids", {}).get(host, ""))

    @property
    def endpoints(self):
        return self.cfg["office_endpoints"] + self.cfg["home_endpoints"]


@pytest.fixture(scope="session")
def cfg():
    return CFG


@pytest.fixture(scope="session")
def lab():
    return Lab(CFG)


def pytest_generate_tests(metafunc):
    """Run a test once per machine of the kind named by its fixture argument."""
    groups = {
        "session_host": CFG["session_hosts"],
        "office_endpoint": CFG["office_endpoints"],
        "home_endpoint": CFG["home_endpoints"],
        "endpoint": CFG["office_endpoints"] + CFG["home_endpoints"],
        "login_host": CFG["session_hosts"] + CFG["office_endpoints"] + CFG["home_endpoints"],
    }
    for arg, names in groups.items():
        if arg in metafunc.fixturenames:
            metafunc.parametrize(arg, names)


def pytest_collection_modifyitems(config, items):
    sim = CFG["mode"] == "sim"
    for item in items:
        if item.get_closest_marker("lab_only") and sim:
            item.add_marker(pytest.mark.skip(reason="needs the real lab (real hardware/VMs)"))
        if item.get_closest_marker("sim_only") and not sim:
            item.add_marker(pytest.mark.skip(reason="simulation-only check"))
