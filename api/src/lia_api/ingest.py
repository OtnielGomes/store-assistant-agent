"""One-shot ingest entrypoint.

Policy loading lives here until a shared ingest service owns every loader.
That service should call ``run_ingest`` or ``load_policy_chunks`` directly.
"""

import psycopg

from lia_api.policies import Embedder, load_policy_chunks, openai_embedder
from lia_api.settings import load_settings


def run_ingest(connection: psycopg.Connection, embed: Embedder) -> int:
    """Load every dataset this entrypoint owns. Currently policies only."""
    return load_policy_chunks(connection, embed)


def main() -> None:
    settings = load_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is required")
    with psycopg.connect(settings.database_url) as connection:
        stored = run_ingest(connection, openai_embedder(settings))
    print(f"Stored {stored} policy chunks")


if __name__ == "__main__":
    main()
