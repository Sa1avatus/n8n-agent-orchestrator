"""Action catalogue and argument validation for board ops commands."""

from __future__ import annotations

from claude_runner.board_ops_actions import ACTIONS, validate_action


def test_every_whitelist_action_present_and_nothing_else():
    assert set(ACTIONS) == {
        "stop_mission",
        "pause_at_boundary",
        "resume",
        "restart_queue",
        "restart_queue_watch",
        "restart_docker_watchdog",
        "restart_tg_relay",
        "rebuild_service",
        "docker_update_memory",
        "set_llama_preset",
        "switch_model_pair",
        "send_test_notification",
    }


def test_dangerous_actions_marked():
    dangerous = {name for name, entry in ACTIONS.items() if entry["dangerous"]}
    assert dangerous == {
        "stop_mission",
        "pause_at_boundary",
        "restart_queue",
        "restart_queue_watch",
        "restart_docker_watchdog",
        "restart_tg_relay",
        "rebuild_service",
        "docker_update_memory",
        "set_llama_preset",
        "switch_model_pair",
    }
    # the two safe actions are repeatable
    assert ACTIONS["resume"]["repeatable"] is True
    assert ACTIONS["send_test_notification"]["repeatable"] is True


def test_rebuild_service_accepts_only_whitelisted_services():
    for service in ("claude-runner", "ahawr-dashboard", "ahawr-retrieval", "ahawr-litellm"):
        assert validate_action("rebuild_service", {"service": service}) == []
    # rejected: a service not in the whitelist
    assert validate_action("rebuild_service", {"service": "llama-cpp-server"})


def test_docker_update_memory_services_and_gb_range():
    assert validate_action("docker_update_memory", {"service": "claude-runner", "gb": 2}) == []
    assert validate_action("docker_update_memory", {"service": "ahawr-retrieval", "gb": 8}) == []
    # rejected: service outside the allowed set
    assert validate_action("docker_update_memory", {"service": "ahawr-litellm", "gb": 4})
    # rejected: gb below or above the 2-8 range
    assert validate_action("docker_update_memory", {"service": "claude-runner", "gb": 1})
    assert validate_action("docker_update_memory", {"service": "claude-runner", "gb": 9})
    # rejected: wrong type
    assert validate_action("docker_update_memory", {"service": "claude-runner", "gb": 2.5})


def test_set_llama_preset_accepts_only_whitelisted_presets():
    assert validate_action("set_llama_preset", {"preset": "enable_thinking"}) == []
    assert validate_action("set_llama_preset", {"preset": "ensure_prod_preset"}) == []
    assert validate_action("set_llama_preset", {"preset": "no_such_preset"})


def test_switch_model_pair_requires_a_registered_pair_id():
    pairs = [
        {"id": "gpt5", "worker": "w", "reviewer": "r", "architect": "a", "providers": ["p"]},
    ]
    assert validate_action("switch_model_pair", {"pair": "gpt5"}, pairs=pairs) == []
    # rejected: a pair id not in the setting
    assert validate_action("switch_model_pair", {"pair": "unknown-pair"}, pairs=pairs)
    # rejected: a non-string pair
    assert validate_action("switch_model_pair", {"pair": 7}, pairs=pairs)


def test_unknown_action_and_unknown_or_missing_args():
    # unknown action
    assert validate_action("drop_container", {})
    # unknown argument for a no-arg action
    assert validate_action("resume", {"service": "x"})
    # missing required argument
    assert validate_action("rebuild_service", {})
    # args must be a dict
    assert validate_action("resume", "not-a-dict")


def test_path_separators_and_shell_metacharacters_rejected():
    # path separator inside an otherwise allowed-looking value
    assert validate_action("rebuild_service", {"service": "claude-runner|x"})
    # shell metacharacter
    assert validate_action("switch_model_pair", {"pair": "p1;p2"},
                          pairs=[{"id": "p1;p2", "worker": "w", "reviewer": "r",
                               "architect": "a", "providers": ["p"]}])
    # backslash
    assert validate_action("set_llama_preset", {"preset": "a\\b"})
    # redirection
    assert validate_action("docker_update_memory",
                          {"service": "claude-runner; rm -rf /"})


def test_each_entry_carries_the_required_keys():
    for name, entry in ACTIONS.items():
        assert set(entry) == {
            "args", "dangerous", "requires_boundary", "repeatable",
            "consequences", "command_preview",
        }, name
        assert callable(entry["consequences"])
        assert callable(entry["command_preview"])
        # both produce a string even with an empty args dict
        assert isinstance(entry["consequences"]({}), str)
        assert isinstance(entry["command_preview"]({}), str)
