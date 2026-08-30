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

# Max tokens for short evaluation calls (Socratic answer grading).
LLM_MAX_TOKENS_EVALUATION = 500

# -- EDS Formula Weights -------------------------------------------------------

# Node coverage weight in the EDS formula.
EDS_ALPHA = 0.4

# Edge (causal link) coverage weight in the EDS formula.
EDS_BETA = 0.6

# Generativity bonus weight for novel extensions beyond expected path.
EDS_GAMMA = 0.15

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
