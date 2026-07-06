#!/usr/bin/env python3
"""Unit tests for pure functions in tdf.py - no network required."""
import pytest
from tdf import (
    fmt_time, fmt_gap, truncate, parse_iso_time,
    format_rankings_table, format_stages, format_teams,
    format_jerseys, format_stage_profile, format_checkpoints,
    format_speed_segments, AsoSource
)
from datetime import datetime, timezone


class TestFmtTime:
    def test_zero(self):
        assert fmt_time(0) == "00:00:00.000"

    def test_standard(self):
        assert fmt_time(3723500) == "01:02:03.500"

    def test_large(self):
        assert fmt_time(36000000) == "10:00:00.000"

    def test_negative(self):
        assert fmt_time(-100) == "—"

    def test_none(self):
        assert fmt_time(None) == "—"


class TestFmtGap:
    def test_zero(self):
        assert fmt_gap(0) == ""

    def test_positive_seconds(self):
        assert fmt_gap(1500) == "+1.500s"

    def test_positive_minutes(self):
        assert fmt_gap(65000) == "+1m05"

    def test_negative(self):
        result = fmt_gap(-65000)
        assert "-" in result
        assert "1m05" in result


class TestTruncate:
    def test_short_string(self):
        assert truncate("hi", 5) == "hi"

    def test_exact_length(self):
        assert truncate("hello", 5) == "hello"

    def test_long_string(self):
        result = truncate("hello world", 8)
        assert len(result) == 8
        assert result.endswith("…")

    def test_single_char(self):
        assert truncate("abc", 1) == "…"


class TestParseIsoTime:
    def test_valid_iso(self):
        result = parse_iso_time("2026-07-05T12:00:00Z")
        assert result is not None
        assert result.year == 2026

    def test_with_timezone(self):
        result = parse_iso_time("2026-07-05T12:00:00+02:00")
        assert result is not None

    def test_invalid(self):
        assert parse_iso_time("not a date") is None

    def test_none(self):
        assert parse_iso_time(None) is None


class TestCleanTelemetry:
    def test_empty_riders(self):
        aso = AsoSource()
        tel = {"Riders": [], "RaceStatus": False, "YGPW": []}
        assert aso.clean_telemetry(tel) == []

    def test_dedup_by_bib(self):
        aso = AsoSource()
        tel = {
            "Riders": [
                {"Bib": 1, "kmToFinish": 50.0},
                {"Bib": 1, "kmToFinish": 50.0},
                {"Bib": 2, "kmToFinish": 50.0},
            ],
            "RaceStatus": False,
            "YGPW": [],
        }
        result = aso.clean_telemetry(tel)
        assert len(result) == 2
        assert result[0]["Bib"] == 1
        assert result[1]["Bib"] == 2

    def test_no_bib_filtered(self):
        aso = AsoSource()
        tel = {
            "Riders": [
                {"Bib": None, "kmToFinish": 50.0},
                {"Bib": 1, "kmToFinish": 50.0},
            ],
            "RaceStatus": False,
            "YGPW": [],
        }
        result = aso.clean_telemetry(tel)
        assert len(result) == 1
        assert result[0]["Bib"] == 1


class TestFormatRankingsTable:
    def test_empty_rankings(self):
        aso = AsoSource()
        result = format_rankings_table(aso, [], 0)
        assert "Pos" in result
        assert "---" in result

    def test_with_mock_data(self):
        aso = AsoSource()
        mock = [
            {"position": 1, "bib": 1, "absolute": 3723500, "relative": 0},
        ]
        result = format_rankings_table(aso, mock, 0)
        assert "01:02:03.500" in result
        assert "1" in result  # bib number


class TestGetRider:
    def test_nonexistent_bib(self):
        aso = AsoSource()
        assert aso.get_rider(99999) is None

    def test_get_all_riders_returns_dict(self):
        aso = AsoSource()
        result = aso.get_all_riders()
        assert isinstance(result, dict)
