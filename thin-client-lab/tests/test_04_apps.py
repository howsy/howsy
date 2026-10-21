"""Checklist: every other call-center app opens (app inventory reachability).
Functional checks per app are manual: docs/MANUAL_TESTS.md (A-1)."""
from pathlib import Path

import pytest
import yaml

from conftest import CFG, TESTS
from lib import http_get

_apps_path = TESTS / CFG["apps_file"]
APPS = yaml.safe_load(_apps_path.read_text())["apps"] if _apps_path.exists() else []
_GROUPS = {"session_hosts": "session_hosts", "office_endpoints": "office_endpoints",
           "home_endpoints": "home_endpoints"}
CASES = [(app, host) for app in APPS for g in app["where"] for host in CFG[_GROUPS[g]]]


@pytest.mark.skipif(not APPS, reason=f"no app inventory at {_apps_path}")
@pytest.mark.parametrize("app,host", CASES, ids=[f"{a['name']}@{h}" for a, h in CASES])
def test_app_reachable(lab, app, host):
    status, body = http_get(lab.host(host), app["url"])
    assert status is not None and status < 400, f"{app['url']} from {host}: {status} {body[:200]}"
    if app.get("expect"):
        assert app["expect"] in body
