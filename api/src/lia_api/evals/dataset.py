"""Upload the golden JSONL as a LangSmith dataset. The file wins over remote examples."""

from langsmith.utils import LangSmithNotFoundError

from lia_api.evals.cases import DATASET_NAME, GoldenCase

_DESCRIPTION = (
    "Lia golden set version 1. Forty customer conversations with expected tool, "
    "required reply values, handoff, and no phone or e-mail. The repository JSONL "
    "is the source of truth."
)


def sync_dataset(client: object, cases: list[GoldenCase], dataset_name: str = DATASET_NAME) -> str:
    """Create or replace the dataset examples so they match the repository file."""
    try:
        dataset = client.read_dataset(dataset_name=dataset_name)  # type: ignore[attr-defined]
    except LangSmithNotFoundError:
        dataset = client.create_dataset(  # type: ignore[attr-defined]
            dataset_name,
            description=_DESCRIPTION,
            metadata={"golden_version": 1},
        )
    existing = list(client.list_examples(dataset_id=dataset.id))  # type: ignore[attr-defined]
    if existing:
        client.delete_examples(  # type: ignore[attr-defined]
            [example.id for example in existing],
            hard_delete=True,
        )
    client.create_examples(  # type: ignore[attr-defined]
        dataset_id=dataset.id,
        examples=[case.as_example() for case in cases],
    )
    return str(dataset.id)
