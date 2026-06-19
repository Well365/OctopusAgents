"""Tests for tg_session_control — session command → inject action mapping."""
from __future__ import annotations

import tg_session_control as sc


def test_stop_is_single_esc_key():
    a = sc.resolve_session_command("/stop")
    assert a == sc.InjectAction(kind="key", payload="esc")


def test_reset_clears():
    assert sc.resolve_session_command("/reset") == sc.InjectAction(kind="text", payload="/clear")


def test_compact():
    assert sc.resolve_session_command("/compact") == sc.InjectAction(kind="text", payload="/compact")


def test_model_with_valid_alias():
    assert sc.resolve_session_command("/model", "opus") == sc.InjectAction(kind="text", payload="/model opus")


def test_model_without_arg_returns_none():
    assert sc.resolve_session_command("/model") is None


def test_model_invalid_alias_returns_none():
    assert sc.resolve_session_command("/model", "gpt9") is None


def test_think_maps_to_effort():
    assert sc.resolve_session_command("/think", "high") == sc.InjectAction(kind="text", payload="/effort high")


def test_think_without_arg_returns_none():
    assert sc.resolve_session_command("/think") is None


def test_think_invalid_level_returns_none():
    assert sc.resolve_session_command("/think", "turbo") is None


def test_unknown_command_returns_none():
    assert sc.resolve_session_command("/bogus") is None


def test_session_usage_lists_models():
    u = sc.session_usage("/model")
    assert "opus" in u and "sonnet" in u


def test_session_usage_lists_effort_levels():
    u = sc.session_usage("/think")
    assert "high" in u and "max" in u
