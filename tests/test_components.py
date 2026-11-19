from __future__ import annotations

import json

from screen_bridge.tasks import load_policy, policy_from_mapping


def test_policy_defaults_to_confirmation():
    policy = policy_from_mapping({"task": "fixture"})
    assert policy.name == "fixture"
    assert policy.auto_execute is False
    assert "click" in policy.allowed_actions


def test_json_policy_round_trip(tmp_path):
    path = tmp_path / "task.json"
    path.write_text(json.dumps({"name": "mole", "auto_execute": True}), encoding="utf-8")
    policy = load_policy(path)
    assert policy.name == "mole"
    assert policy.auto_execute is True
