"""Checklist: golden image deploys to a new device without manual steps.

Full check: `make golden-image` destroys an endpoint, recreates it from the bare
OS image, runs the playbook once and re-runs the endpoint tests.
This module checks the other half: re-applying the image changes nothing."""
import os
import re
import subprocess

import pytest

from conftest import CFG, ROOT

pytestmark = [pytest.mark.slow, pytest.mark.endpoint]


def test_playbook_is_idempotent_on_endpoints():
    env = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / "ansible" / "ansible.cfg"))
    r = subprocess.run(
        ["ansible-playbook", "-i", str(ROOT / CFG["ansible_inventory"]), "site.yml", "--limit", "endpoints"],
        cwd=ROOT / "ansible", env=env, capture_output=True, text=True, timeout=1800)
    assert r.returncode == 0, r.stdout[-3000:]
    recap = r.stdout[r.stdout.rindex("PLAY RECAP"):]
    changed = {h: int(n) for h, n in re.findall(r"^(\S+)\s+:.*changed=(\d+)", recap, re.M)}
    assert changed and all(n == 0 for n in changed.values()), recap
