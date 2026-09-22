"""
Centralized constants for the Epistemy M3 platform.

All magic strings and numbers that appear across the codebase are collected here
so they can be changed in one place.
"""

# -- LLM Model IDs ------------------------------------------------------------

# Primary LLM used for concept extraction, question generation, and evaluation.
LLM_MODEL_ID = "qwen.qwen3-32b-v1:0"

# Embedding model for vector search over course chunks.
EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"

# -- Chunk Limits --------------------------------------------------------------

# Maximum chunks per material for incremental graph build, or total for full rebuild.
MAX_CHUNKS_FOR_GRAPH = 30

# Maximum number of chunks concatenated for question generation prompts.
MAX_CHUNKS_FOR_GENERATION = 15

# -- Question / Answer Limits --------------------------------------------------

# Upper bound on how many questions can be generated in a single request.
MAX_QUESTION_COUNT = 50

# Maximum character length accepted for a student answer submission.
MAX_ANSWER_LENGTH = 10000

# -- Inference Defaults --------------------------------------------------------

# Max tokens returned by the LLM for graph/question generation calls.
LLM_MAX_TOKENS_GENERATION = 4000

# Max tokens for the concept-graph extraction call. This packs concepts +
# per-concept depth-tagged question banks (up to ~7 questions each) into one
# JSON response, which for a large doc overflowed an 8000-token cap and
# truncated mid-JSON — the parse then failed and the doc got NO concepts. Sized
# well above that so the JSON completes for a full 5-20 concept extraction.
LLM_MAX_TOKENS_GRAPH = 16000

# Max tokens for short evaluation calls (Socratic answer grading).
LLM_MAX_TOKENS_EVALUATION = 500

# -- EDS Formula Weights -------------------------------------------------------

# Node coverage weight in the EDS formula.
EDS_ALPHA = 0.4

# Edge (causal link) coverage weight in the EDS formula.
EDS_BETA = 0.6

# Generativity (novel-insight) weight. Set to 0: "Novel Insight" was retired from
# the rubric, so gen no longer affects the score — node + edge are the only signals.
EDS_GAMMA = 0.0


def compute_eds(node_score: float, edge_score: float, gen_score: float = 0.0) -> float:
    """Correctness-only EDS in [0, 1] = clamp01(α·node + β·edge + γ·gen).

    Two scored signals: concepts named correctly (node) and multi-concept / causal
    links correct (edge). γ is 0, so ``gen_score`` (novel insight) is accepted for
    signature/telemetry compatibility but does not affect the score. There is no
    authenticity/recitation gate — genuineness is not part of the rubric.
    """
    eds = EDS_ALPHA * node_score + EDS_BETA * edge_score + EDS_GAMMA * gen_score
    return round(min(1.0, max(0.0, eds)), 4)


# -- Upload / Ingestion Limits -------------------------------------------------

# Largest single file accepted for upload. Above this, presign rejects with a
# stated limit instead of letting a huge upload hang the ingest pipeline.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

# Largest PDF (in pages) the text extractor will process. Above this, ingestion
# fails with a stated limit rather than timing out with no message.
MAX_PDF_PAGES = 300

# -- Organization Defaults -----------------------------------------------------

# Default organization name used when no explicit org header is provided.
DEFAULT_ORG_NAME = "epistemy"
