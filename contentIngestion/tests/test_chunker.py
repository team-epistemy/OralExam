"""T5: contiguous indices, structured units kept whole, fallback windows."""
from epistemy_m3.chunking import Chunker, ExtractedUnit
from epistemy_m3.models import ChunkPosition


def test_chunk_index_is_contiguous():
    units = [ExtractedUnit(text=f"Slide {i} body text.",
                           position=ChunkPosition(slide_no=i)) for i in range(1, 6)]
    chunks = Chunker().chunk(units)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_structured_unit_kept_whole():
    unit = ExtractedUnit(text="Short heading section body.",
                         position=ChunkPosition(heading_path=["Intro"]))
    chunks = Chunker().chunk([unit])
    assert len(chunks) == 1
    assert chunks[0].position.heading_path == ["Intro"]


def test_fallback_windows_long_unstructured_text():
    long_text = " ".join(f"sentence number {i}." for i in range(400))
    unit = ExtractedUnit(text=long_text, structured=False)
    chunks = Chunker(window=100, overlap=20).chunk([unit])
    assert len(chunks) > 1
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
