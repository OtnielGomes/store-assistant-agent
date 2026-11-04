"""Policy manual loading and hybrid search.

The reviewed Markdown is the source of truth (ADR-0005). A shared ingest
service can call ``load_policy_chunks`` with an open connection. Embeddings
are produced by an injected function so tests never call OpenAI.
"""

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psycopg

from lia_api.settings import Settings

EMBEDDING_DIMENSIONS = 1536
Embedder = Callable[[list[str]], list[list[float]]]

_MANUAL_PATH = Path(__file__).resolve().parents[3] / "data" / "manual" / "politicas_da_loja.md"
_HEADING = re.compile(r"^#{2,3} (\d+\.\d+|\d+)\.?\s+(.+?)\s*$", re.MULTILINE)
_RRF_K = 60
_CANDIDATES = 20

_SCHEMA_STATEMENTS = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    """
    CREATE TABLE IF NOT EXISTS policy_chunks (
        section_number text PRIMARY KEY,
        title text NOT NULL,
        content text NOT NULL,
        search_vector tsvector NOT NULL,
        embedding vector(1536) NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS policy_chunks_search_idx
        ON policy_chunks USING gin (search_vector)
    """,
)

_UPSERT = """
INSERT INTO policy_chunks (section_number, title, content, search_vector, embedding)
VALUES (
    %s,
    %s,
    %s,
    setweight(to_tsvector('portuguese', %s), 'A')
        || setweight(to_tsvector('portuguese', %s), 'B'),
    %s::vector
)
ON CONFLICT (section_number) DO UPDATE SET
    title = EXCLUDED.title,
    content = EXCLUDED.content,
    search_vector = EXCLUDED.search_vector,
    embedding = EXCLUDED.embedding
"""

_SEARCH = """
WITH lexical_query AS (
    SELECT regexp_replace(
        plainto_tsquery('portuguese', %(query)s)::text,
        ' & ',
        ' | ',
        'g'
    )::tsquery AS query
),
lexical AS (
    SELECT policy_chunks.section_number,
           ROW_NUMBER() OVER (
               ORDER BY ts_rank_cd(policy_chunks.search_vector, lexical_query.query) DESC
           ) AS rank
    FROM policy_chunks
    CROSS JOIN lexical_query
    WHERE policy_chunks.search_vector @@ lexical_query.query
    ORDER BY rank
    LIMIT %(candidates)s
),
semantic AS (
    SELECT section_number,
           ROW_NUMBER() OVER (
               ORDER BY embedding <=> %(embedding)s::vector
           ) AS rank
    FROM policy_chunks
    ORDER BY embedding <=> %(embedding)s::vector
    LIMIT %(candidates)s
),
fused AS (
    SELECT section_number, SUM(reciprocal_rank) AS score
    FROM (
        SELECT section_number, 1.0 / (%(rrf_k)s + rank) AS reciprocal_rank FROM lexical
        UNION ALL
        SELECT section_number, 1.0 / (%(rrf_k)s + rank) AS reciprocal_rank FROM semantic
    ) ranked
    GROUP BY section_number
)
SELECT policy_chunks.section_number, policy_chunks.title, policy_chunks.content
FROM fused
JOIN policy_chunks ON policy_chunks.section_number = fused.section_number
ORDER BY fused.score DESC, policy_chunks.section_number
LIMIT %(limit)s
"""


@dataclass(frozen=True)
class PolicySection:
    section_number: str
    title: str
    content: str


@dataclass(frozen=True)
class PolicyHit:
    section_number: str
    title: str
    content: str


def parse_policy_manual(markdown: str) -> list[PolicySection]:
    """Split the manual into one section per numbered heading."""
    matches = list(_HEADING.finditer(markdown))
    sections: list[PolicySection] = []
    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        sections.append(
            PolicySection(
                section_number=match.group(1),
                title=match.group(2).strip(),
                content=markdown[body_start:body_end].strip(),
            )
        )
    return sections


def openai_embedder(settings: Settings) -> Embedder:
    """OpenAI embeddings using OPENAI_EMBEDDING_MODEL. Not used by tests."""
    from langchain_openai import OpenAIEmbeddings

    client = OpenAIEmbeddings(
        model=settings.openai_embedding_model,
        openai_api_key=settings.openai_api_key or None,
        dimensions=EMBEDDING_DIMENSIONS,
    )

    def embed(texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [[float(value) for value in vector] for vector in client.embed_documents(texts)]

    return embed


def load_policy_chunks(
    connection: psycopg.Connection,
    embed: Embedder,
    manual_path: Path | None = None,
) -> int:
    """Store one chunk per numbered section. Safe to call again."""
    path = manual_path or _default_manual_path()
    sections = parse_policy_manual(path.read_text(encoding="utf-8"))
    if not sections:
        raise ValueError(f"No numbered sections in {path}")
    documents = [_embedding_text(section) for section in sections]
    vectors = embed(documents)
    _validate_vectors(vectors, len(sections))

    with connection.cursor() as cursor:
        for statement in _SCHEMA_STATEMENTS:
            cursor.execute(statement)
        for section, vector in zip(sections, vectors, strict=True):
            cursor.execute(
                _UPSERT,
                (
                    section.section_number,
                    section.title,
                    section.content,
                    section.title,
                    section.content,
                    _vector_literal(vector),
                ),
            )
        cursor.execute(
            "DELETE FROM policy_chunks WHERE NOT (section_number = ANY(%s))",
            ([section.section_number for section in sections],),
        )
        cursor.execute("SELECT COUNT(*) FROM policy_chunks")
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("Policy chunk count query returned no row")
    return int(row[0])


def ingest_policies(database_url: str, embed: Embedder, manual_path: Path | None = None) -> int:
    """Load the policy manual from a database URL."""
    with psycopg.connect(database_url) as connection:
        return load_policy_chunks(connection, embed, manual_path)


def format_policy_hits(hits: list[PolicyHit]) -> str:
    if not hits:
        return "Nenhuma seção do manual corresponde a essa pergunta."
    return "\n\n".join(
        f"Seção {hit.section_number} — {hit.title}\n{hit.content}".strip() for hit in hits
    )


def build_policy_search_tool(database_url: str, embed: Embedder):
    """LangGraph tool that runs the real hybrid search."""
    from langchain_core.tools import tool

    @tool("search_policies")
    def run_policy_search(query: str) -> str:
        """Busca seções do manual interno de políticas da loja.

        Use para horário de funcionamento, pagamento, trocas, devoluções, frete,
        garantia, privacidade e campanhas descritas no manual, como a Black Friday.
        """
        return format_policy_hits(search_policies(database_url, query, embed, limit=5))

    return run_policy_search


def search_policies(
    database_url: str,
    query: str,
    embed: Embedder,
    *,
    limit: int = 5,
) -> list[PolicyHit]:
    """Hybrid Portuguese full-text and vector search, fused with reciprocal rank."""
    vector = embed([query])
    _validate_vectors(vector, 1)
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                _SEARCH,
                {
                    "query": query,
                    "embedding": _vector_literal(vector[0]),
                    "candidates": _CANDIDATES,
                    "rrf_k": _RRF_K,
                    "limit": limit,
                },
            )
            rows = cursor.fetchall()
    return [
        PolicyHit(section_number=section_number, title=title, content=content)
        for section_number, title, content in rows
    ]


def _default_manual_path() -> Path:
    configured = os.environ.get("DATA_DIR")
    if configured:
        return Path(configured) / "manual" / "politicas_da_loja.md"
    return _MANUAL_PATH


def _embedding_text(section: PolicySection) -> str:
    # Repeat the title so a heading match outranks a passing mention in another section.
    return f"{section.title}\n{section.title}\n{section.title}\n{section.content}"


def _validate_vectors(vectors: list[list[float]], expected: int) -> None:
    if len(vectors) != expected:
        raise ValueError(f"Embedder returned {len(vectors)} vectors for {expected} texts")
    for vector in vectors:
        if len(vector) != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"Expected {EMBEDDING_DIMENSIONS}-dimensional embeddings, got {len(vector)}"
            )


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(format(value, ".8f") for value in values) + "]"
