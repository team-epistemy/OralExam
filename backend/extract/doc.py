"""DOC extractor: legacy binary Word via the `antiword` system binary.

python-docx only reads .docx (OOXML); Word 97-2003 .doc is an OLE blob.
antiword (installed in the image) converts it to plain text, which then falls
through to MarkdownExtractor's paragraph splitting.
"""
from __future__ import annotations
import subprocess
import tempfile
from typing import List

from backend.chunking import ExtractedUnit
from backend.extract.markdown import MarkdownExtractor


class DocExtractor:
    """antiword subprocess → plain text → markdown paragraph units."""

    def extract(self, data: bytes) -> List[ExtractedUnit]:
        with tempfile.NamedTemporaryFile(suffix=".doc") as f:
            f.write(data)
            f.flush()
            try:
                out = subprocess.run(
                    ["antiword", f.name], capture_output=True, timeout=60)
            except FileNotFoundError:
                raise ValueError("antiword is not installed; cannot read .doc files")
        if out.returncode != 0:
            raise ValueError(
                "Could not read this .doc file. Re-save it as .docx and upload again.")
        text = out.stdout.decode("utf-8", errors="replace")
        return MarkdownExtractor().extract(text.encode("utf-8"))
