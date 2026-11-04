"""Golden-set evaluation for Lia. The JSONL file is the source of truth."""

from lia_api.evals.cases import GOLDEN_SET_PATH, load_cases
from lia_api.evals.score import Assessment, Verdict, assess, verdict

__all__ = [
    "GOLDEN_SET_PATH",
    "Assessment",
    "Verdict",
    "assess",
    "load_cases",
    "verdict",
]
