"""T7: heading_path tracking and paragraph fallback."""
from epistemy_m3.extract.markdown import MarkdownExtractor


def test_heading_path_tracked():
    md = b"# Title\n\nintro\n\n## Sub A\n\nbody a\n\n## Sub B\n\nbody b\n"
    units = MarkdownExtractor().extract(md)
    paths = [u.position.heading_path for u in units]
    assert ["Title"] in paths
    assert ["Title", "Sub A"] in paths
    assert ["Title", "Sub B"] in paths


def test_plain_text_falls_through_to_paragraphs():
    md = b"first paragraph here.\n\nsecond paragraph here.\n"
    units = MarkdownExtractor().extract(md)
    assert len(units) == 2
    assert all(u.structured is False for u in units)
