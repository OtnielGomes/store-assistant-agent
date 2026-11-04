"""One LangSmith metric per check. The judge is injected by the caller."""

from collections.abc import Callable

from lia_api.evals.score import assess

Judge = Callable[[str, dict[str, object], list[str]], bool]


def _observed(run: object) -> dict[str, object]:
    if isinstance(run, dict):
        outputs = run.get("outputs") or {}
    else:
        outputs = getattr(run, "outputs", None) or {}
    return outputs if isinstance(outputs, dict) else {}


def _expected(example: object) -> dict[str, object]:
    if isinstance(example, dict):
        outputs = example.get("outputs") or example.get("reference_outputs") or {}
    else:
        outputs = getattr(example, "outputs", None)
        if not outputs:
            outputs = getattr(example, "reference_outputs", None) or {}
    return outputs if isinstance(outputs, dict) else {}


def _case_id(example: object) -> str:
    if isinstance(example, dict):
        metadata = example.get("metadata")
    else:
        metadata = getattr(example, "metadata", None)
    if isinstance(metadata, dict) and isinstance(metadata.get("id"), str):
        return metadata["id"]
    return ""


def _messages(example: object) -> list[str]:
    if isinstance(example, dict):
        inputs = example.get("inputs")
    else:
        inputs = getattr(example, "inputs", None)
    if not isinstance(inputs, dict):
        return []
    messages = inputs.get("messages") or []
    if not isinstance(messages, list):
        return []
    return [item for item in messages if isinstance(item, str)]


def _checked(run: object, example: object):
    return assess(
        _observed(run),
        _expected(example),
        judge_passes=True,
        case_id=_case_id(example),
    )


def expected_tool(run: object, example: object) -> dict[str, object]:
    result = _checked(run, example)
    score = 1 if result.tool_ok else 0
    return {"key": "expected_tool", "score": score, "comment": result.tool_comment}


def required_values(run: object, example: object) -> dict[str, object]:
    result = _checked(run, example)
    score = 1 if result.values_ok else 0
    return {"key": "required_values", "score": score, "comment": result.values_comment}


def handoff(run: object, example: object) -> dict[str, object]:
    result = _checked(run, example)
    score = 1 if result.handoff_ok else 0
    return {"key": "handoff", "score": score, "comment": result.handoff_comment}


def no_contact(run: object, example: object) -> dict[str, object]:
    result = _checked(run, example)
    score = 1 if result.contact_ok else 0
    return {"key": "no_contact", "score": score, "comment": result.contact_comment}


def plain_text(run: object, example: object) -> dict[str, object]:
    result = _checked(run, example)
    score = 1 if result.plain_ok else 0
    return {"key": "plain_text", "score": score, "comment": result.plain_comment}


def wrong_price(run: object, example: object) -> dict[str, object]:
    result = _checked(run, example)
    score = 0 if result.wrong_price else 1
    return {"key": "wrong_price", "score": score, "comment": result.price_comment}


def make_portuguese_judge(judge: Judge):
    """LLM judge for correctness and tone. Tests pass a fake judge."""

    def portuguese_judge(run: object, example: object) -> dict[str, object]:
        reply = _observed(run).get("reply")
        text = reply if isinstance(reply, str) else ""
        passed = judge(text, _expected(example), _messages(example))
        if passed:
            comment = "Correct Portuguese store reply."
        else:
            comment = "Judge rejected correctness or tone."
        return {"key": "portuguese_judge", "score": 1 if passed else 0, "comment": comment}

    return portuguese_judge


def make_evaluators(judge: Judge) -> list[object]:
    return [
        expected_tool,
        required_values,
        handoff,
        no_contact,
        plain_text,
        wrong_price,
        make_portuguese_judge(judge),
    ]
