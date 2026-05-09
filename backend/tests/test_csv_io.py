"""CSV io 純邏輯（_strip_bom、_detect_dialect、export round-trip）。"""

from __future__ import annotations

import csv
import io

from app.services.csv_io import _detect_dialect, _strip_bom


def test_strip_bom_only_leading():
    assert _strip_bom("﻿hello") == "hello"
    assert _strip_bom("hello") == "hello"
    assert _strip_bom("﻿") == ""


def test_detect_dialect_comma():
    sample = "ip,hostname,mac\n10.0.0.1,foo,00:11:22:33:44:55\n"
    d = _detect_dialect(sample)
    assert d.delimiter == ","


def test_detect_dialect_semicolon():
    sample = "ip;hostname;mac\n10.0.0.1;foo;00:11:22:33:44:55\n"
    d = _detect_dialect(sample)
    assert d.delimiter == ";"


def test_detect_dialect_tab():
    sample = "ip\thostname\tmac\n10.0.0.1\tfoo\t00:11:22:33:44:55\n"
    d = _detect_dialect(sample)
    assert d.delimiter == "\t"


def test_detect_dialect_fallback():
    # 完全沒分隔符號 → fallback excel（comma）
    sample = "ipheaderonly\n10.0.0.1\n"
    d = _detect_dialect(sample)
    assert d.delimiter == ","


def test_dictreader_handles_arbitrary_column_order():
    """確保我們不依賴欄位順序（phpIPAM 的痛點）。"""
    csv_text = "mac,description,ip,hostname\nDE:AD:BE:EF:00:01,test row,10.0.0.42,foo\n"
    reader = csv.DictReader(io.StringIO(csv_text))
    row = next(reader)
    assert row["ip"] == "10.0.0.42"
    assert row["hostname"] == "foo"
    assert row["mac"] == "DE:AD:BE:EF:00:01"
