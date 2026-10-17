"""crossroads: PASSAGE:/CHOICES: parsing, choice/guidance provenance,
one-tx commits with image SetMeta, degradation paths."""
import pytest

from strategies.crossroads import parse_crossroads_output


def test_parses_standard_format():
    text = ("PASSAGE:\nShe stepped inside, and the dust rose to meet her.\n\n"
            "CHOICES:\n- Light a match\n- Call into the dark\n- Take the stairs")
    passage, choices = parse_crossroads_output(text)
    assert passage == "She stepped inside, and the dust rose to meet her."
    assert choices == ["Light a match", "Call into the dark", "Take the stairs"]


def test_numbered_choices_accepted():
    text = "PASSAGE:\nA.\n\nCHOICES:\n1. one\n2. two\n3) three"
    assert parse_crossroads_output(text)[1] == ["one", "two", "three"]


def test_missing_choices_block_degrades():
    passage, choices = parse_crossroads_output("PASSAGE:\nJust the text.")
    assert passage == "Just the text."
    assert choices == []


def test_missing_passage_marker_uses_whole_reply():
    passage, choices = parse_crossroads_output("No markers at all.")
    assert passage == "No markers at all."
    assert choices == []


def test_extra_choices_trimmed_to_three():
    text = "PASSAGE:\nP.\n\nCHOICES:\n- a\n- b\n- c\n- d"
    assert parse_crossroads_output(text)[1] == ["a", "b", "c"]


def test_empty_choice_lines_skipped():
    text = "PASSAGE:\nP.\n\nCHOICES:\n- a\n\n- b\n"
    assert parse_crossroads_output(text)[1] == ["a", "b"]


def test_bare_marker_choice_lines_skipped():
    text = "PASSAGE:\nP.\n\nCHOICES:\n- a\n-\n- b\n- c"
    assert parse_crossroads_output(text)[1] == ["a", "b", "c"]
