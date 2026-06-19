"""Tests for interactive_prompt — detect arrow-select menus + auto-default decision."""
from __future__ import annotations

import interactive_prompt as ip

MENU = (
    "? Project location\n"
    "❯ 1. Point me to the path\n"
    "  2. It's a different repo\n"
    "  3. Describe the bug first\n"
    "  4. Type something.\n"
    "Enter to select · ↑/↓ to navigate · Esc to cancel\n"
)

PLAIN = "Listed 1 directory\nThe working directory is empty.\nDone.\n"


def test_detects_select_menu():
    assert ip.detect_select_prompt(MENU) is True


def test_plain_output_is_not_a_prompt():
    assert ip.detect_select_prompt(PLAIN) is False


def test_footer_without_cursor_is_not_a_prompt():
    # Footer text present but no ❯ cursor → not a live menu
    assert ip.detect_select_prompt("Enter to select · ↑/↓ to navigate\n") is False


def test_empty_is_not_a_prompt():
    assert ip.detect_select_prompt("") is False


def test_should_default_disabled_when_threshold_zero():
    assert ip.should_auto_default(
        is_prompt=True, stable_elapsed=999, threshold=0, stable_key="a", last_fired_key=""
    ) is False


def test_should_default_false_when_not_prompt():
    assert ip.should_auto_default(
        is_prompt=False, stable_elapsed=999, threshold=60, stable_key="a", last_fired_key=""
    ) is False


def test_should_default_false_before_threshold():
    assert ip.should_auto_default(
        is_prompt=True, stable_elapsed=30, threshold=60, stable_key="a", last_fired_key=""
    ) is False


def test_should_default_true_after_threshold_new_prompt():
    assert ip.should_auto_default(
        is_prompt=True, stable_elapsed=61, threshold=60, stable_key="a", last_fired_key="b"
    ) is True


def test_should_default_false_when_already_fired_for_this_prompt():
    assert ip.should_auto_default(
        is_prompt=True, stable_elapsed=61, threshold=60, stable_key="a", last_fired_key="a"
    ) is False
