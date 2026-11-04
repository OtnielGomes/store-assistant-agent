"""Deterministic embedder for policy tests. Never calls OpenAI.

Section 4.1 of the manual contains the phrase "direito de arrependimento".
The paraphrase below shares no keyword with that section, so the vector
places that question on the same axis as the section text. Search itself
stays the real reciprocal rank fusion query.
"""

import hashlib
import re
import unicodedata

DIMENSIONS = 1536

_WITHDRAWAL_PARAPHRASE = "comprei e me arrependi, posso devolver?"
_WITHDRAWAL_PHRASE = "direito de arrependimento"


def _fold(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", stripped.lower()).strip()


def _near_withdrawal_section(text: str) -> bool:
    folded = _fold(text)
    return _WITHDRAWAL_PHRASE in folded or folded == _WITHDRAWAL_PARAPHRASE


_TOKEN = re.compile(r"[a-z0-9]+")


def embed_for_tests(texts: list[str]) -> list[list[float]]:
    """Bag-of-words vectors, plus one axis shared by section 4.1 and its paraphrase."""
    vectors: list[list[float]] = []
    for text in texts:
        folded = _fold(text)
        vector = [0.0] * DIMENSIONS
        for token in _TOKEN.findall(folded):
            digest = hashlib.sha256(token.encode()).digest()
            slot = 1 + int.from_bytes(digest[:4], "big") % (DIMENSIONS - 1)
            vector[slot] += 1.0
        if _near_withdrawal_section(text):
            vector[0] = 8.0
        vectors.append(vector)
    return vectors
