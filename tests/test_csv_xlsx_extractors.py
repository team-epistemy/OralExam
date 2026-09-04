"""CSV + XLSX extractors and their graph-exclusion wiring."""
import io

import pytest

from backend.api.service import detect_source_type
from backend.models import SourceType, NON_GRAPH_SOURCE_TYPES
from backend.extract.base import get_extractor
from backend.extract.csv_ext import CsvExtractor, rows_to_text


def test_detect_source_type_csv_xlsx():
    assert detect_source_type("grades.csv") == SourceType.CSV
    assert detect_source_type("Roster.XLSX") == SourceType.XLSX


def test_csv_and_xlsx_excluded_from_graph_but_pdf_not():
    assert SourceType.CSV in NON_GRAPH_SOURCE_TYPES
    assert SourceType.XLSX in NON_GRAPH_SOURCE_TYPES
    assert SourceType.PDF not in NON_GRAPH_SOURCE_TYPES
    assert SourceType.DOCX not in NON_GRAPH_SOURCE_TYPES


def test_rows_to_text_headed_table():
    rows = [["name", "score"], ["Ada", "95"], ["Bob", "60"]]
    text = rows_to_text(rows)
    assert "name | score" in text
    assert "name: Ada; score: 95" in text
    assert "name: Bob; score: 60" in text


def test_rows_to_text_ignores_blank_rows():
    assert rows_to_text([["", ""], []]) == ""


def test_csv_extractor_yields_units():
    data = b"topic,note\nSupply,ships in 3 days\nDemand,peaks in Q4\n"
    units = CsvExtractor().extract(data)
    assert units, "expected at least one extracted unit"
    joined = "\n".join(u.text for u in units)
    assert "Supply" in joined and "ships in 3 days" in joined


def test_get_extractor_registered_for_csv():
    assert isinstance(get_extractor(SourceType.CSV), CsvExtractor)


def test_xlsx_extractor_yields_units():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["term", "definition"])
    ws.append(["Latency", "time to first byte"])
    buf = io.BytesIO()
    wb.save(buf)

    units = get_extractor(SourceType.XLSX).extract(buf.getvalue())
    assert units
    joined = "\n".join(u.text for u in units)
    assert "Latency" in joined and "time to first byte" in joined
