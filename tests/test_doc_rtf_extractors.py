""".doc/.rtf support: extension mapping + extractor behavior."""
from unittest import mock

import pytest

from backend.models import SourceType
from backend.api.service import detect_source_type, AuthorizationError
from backend.extract.base import get_extractor
from backend.extract.doc import DocExtractor


def test_detect_source_type_maps_doc_and_rtf():
    assert detect_source_type("essay.doc") == SourceType.DOC
    assert detect_source_type("notes.RTF") == SourceType.RTF
    # .docx must still win over the .doc suffix check.
    assert detect_source_type("report.docx") == SourceType.DOCX


def test_detect_source_type_rejects_unsupported():
    with pytest.raises(AuthorizationError):
        detect_source_type("image.png")


def test_dispatch_table_has_doc_and_rtf():
    assert get_extractor(SourceType.DOC) is not None
    assert get_extractor(SourceType.RTF) is not None


def test_rtf_extractor_end_to_end():
    pytest.importorskip("striprtf")
    from backend.extract.rtf import RtfExtractor
    rtf = rb"{\rtf1\ansi first paragraph.\par\par second paragraph.\par}"
    units = RtfExtractor().extract(rtf)
    text = " ".join(u.text for u in units)
    assert "first paragraph" in text
    assert "second paragraph" in text


def test_doc_extractor_missing_antiword_is_friendly():
    with mock.patch("backend.extract.doc.subprocess.run",
                    side_effect=FileNotFoundError):
        with pytest.raises(ValueError, match="antiword is not installed"):
            DocExtractor().extract(b"anything")


def test_doc_extractor_success_via_mock():
    completed = mock.Mock(returncode=0, stdout=b"para one.\n\npara two.\n")
    with mock.patch("backend.extract.doc.subprocess.run",
                    return_value=completed):
        units = DocExtractor().extract(b"\xd0\xcf binary doc")
    text = " ".join(u.text for u in units)
    assert "para one" in text and "para two" in text


def test_doc_extractor_bad_return_code_is_friendly():
    completed = mock.Mock(returncode=1, stdout=b"")
    with mock.patch("backend.extract.doc.subprocess.run",
                    return_value=completed):
        with pytest.raises(ValueError, match="Re-save it as .docx"):
            DocExtractor().extract(b"corrupt")
