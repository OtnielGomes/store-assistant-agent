"""Run the golden set: upload the dataset, record one experiment, print the verdict."""

import os
import uuid
from datetime import datetime

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.postgres import PostgresSaver

from lia_api.clock import FrozenClock
from lia_api.evals.cases import DATASET_NAME, load_cases
from lia_api.evals.dataset import sync_dataset
from lia_api.evals.evaluators import make_evaluators
from lia_api.evals.judge import openai_judge
from lia_api.evals.score import Assessment, Verdict, assess, verdict
from lia_api.graph import build_graph
from lia_api.guardrails import exceeds_message_length, mask_personal_data
from lia_api.handoff import HandoffStore, build_record_handoff_tool
from lia_api.ingest import load_store
from lia_api.model import build_chat_model
from lia_api.policies import ingest_policies, openai_embedder
from lia_api.settings import Settings, load_settings

_PLACEHOLDER_OPENAI = {"", "sk-placeholder"}
_PLACEHOLDER_LANGSMITH = {"", "lsv2-placeholder"}


def run_experiment(settings: Settings | None = None) -> tuple[Verdict, list[Assessment], str]:
    """Upload the JSONL, run Lia once per case, and score the experiment."""
    current = settings or load_settings()
    _require_live_keys(current)
    if not current.database_url:
        raise SystemExit("DATABASE_URL is required to run the golden set.")
    cases = load_cases()
    os.environ["LANGSMITH_API_KEY"] = current.langsmith_api_key
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_PROJECT"] = current.langsmith_project
    from langsmith import Client, evaluate

    client = Client()
    dataset_id = sync_dataset(client, cases)
    load_store(current.database_url)
    ingest_policies(current.database_url, openai_embedder(current))
    judge = openai_judge(current)
    store = HandoffStore(current.database_url)
    store.ensure_schema()
    with PostgresSaver.from_conn_string(current.database_url) as checkpointer:
        checkpointer.setup()
        graph = _graph(current, store, checkpointer)
        results = evaluate(
            lambda inputs: _observe(graph, store, inputs),
            data=DATASET_NAME,
            evaluators=make_evaluators(judge),
            experiment_prefix=DATASET_NAME,
            description=f"Golden set version 1 ({dataset_id}).",
            max_concurrency=1,
            metadata={"golden_version": 1},
            client=client,
        )
        rows = list(results)
    assessments = [_assessment_from_row(row) for row in rows]
    outcome = verdict(assessments)
    location = results.url or results.experiment_name
    return outcome, assessments, location


def _graph(settings: Settings, store: HandoffStore, checkpointer: object):
    clock = FrozenClock(datetime.fromisoformat("2026-11-03T10:00:00-04:00"))
    return build_graph(
        build_chat_model(settings),
        checkpointer,
        [build_record_handoff_tool(store, clock)],
        database_url=settings.database_url,
        embedder=openai_embedder(settings),
        handoff_store=store,
    )


def _observe(graph: object, store: HandoffStore, inputs: dict[str, object]) -> dict[str, object]:
    messages = inputs.get("messages")
    frozen_at = inputs.get("frozen_at")
    if not isinstance(messages, list) or not isinstance(frozen_at, str):
        raise ValueError("Each case needs messages and a frozen instant.")
    thread_id = f"golden-{uuid.uuid4()}"
    clock = FrozenClock(datetime.fromisoformat(frozen_at))
    before = store.latest_id()
    result: dict[str, object] | None = None
    for message in messages:
        if not isinstance(message, str):
            continue
        if exceeds_message_length(message):
            customer_message = "x" * len(message)
        else:
            customer_message = mask_personal_data(message)
        result = graph.invoke(  # type: ignore[attr-defined]
            {"messages": [HumanMessage(content=customer_message)]},
            {"configurable": {"thread_id": thread_id, "clock": clock}},
        )
    if result is None:
        return {"reply": "", "tools": [], "handoff_created": False}
    history = result["messages"]
    created = any(
        item.id > before and item.conversation_id == thread_id for item in store.list_handoffs()
    )
    return {
        "reply": _assistant_text(history),
        "tools": _tool_names(history),
        "handoff_created": created,
    }


def _assistant_text(messages: list[object]) -> str:
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, AIMessage) or message.tool_calls:
            continue
        text = _message_text(message).strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def _tool_names(messages: list[object]) -> list[str]:
    names: list[str] = []
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            name = call.get("name") if isinstance(call, dict) else None
            if isinstance(name, str) and name not in names:
                names.append(name)
    return names


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return ""


def _assessment_from_row(row: dict[str, object]) -> Assessment:
    run = row["run"]
    example = row["example"]
    observed = run.outputs if hasattr(run, "outputs") else {}
    expected = example.outputs if hasattr(example, "outputs") else {}
    if not isinstance(observed, dict):
        observed = {}
    if not isinstance(expected, dict):
        expected = {}
    metadata = getattr(example, "metadata", None) or {}
    case_id = metadata.get("id", "") if isinstance(metadata, dict) else ""
    if not isinstance(case_id, str):
        case_id = ""
    return assess(
        observed,
        expected,
        judge_passes=_judge_passed(row),
        case_id=case_id,
    )


def _judge_passed(row: dict[str, object]) -> bool:
    evaluation = row.get("evaluation_results")
    results = getattr(evaluation, "results", None)
    if results is None and isinstance(evaluation, dict):
        results = evaluation.get("results")
    for item in results or []:
        key = item.key if hasattr(item, "key") else item.get("key")
        score = item.score if hasattr(item, "score") else item.get("score")
        if key == "portuguese_judge":
            return score == 1
    return False


def _require_live_keys(settings: Settings) -> None:
    if settings.openai_api_key.strip() in _PLACEHOLDER_OPENAI:
        raise SystemExit(
            "OPENAI_API_KEY is missing or still the placeholder. "
            "The live golden-set experiment was not started."
        )
    if settings.langsmith_api_key.strip() in _PLACEHOLDER_LANGSMITH:
        raise SystemExit(
            "LANGSMITH_API_KEY is missing or still the placeholder. "
            "The live golden-set experiment was not started."
        )


def format_report(outcome: Verdict, assessments: list[Assessment], location: str) -> str:
    lines = [
        f"Dataset: {DATASET_NAME}",
        f"Experiment: {location}",
        f"Passed: {outcome.passed}/{outcome.total}",
        f"Wrong prices: {outcome.wrong_prices}",
        f"Personal-data leaks: {outcome.personal_data_leaks}",
        f"Missed mandatory handoffs: {outcome.missed_mandatory_handoffs}",
        f"Verdict: {'PASS' if outcome.ok else 'FAIL'}",
    ]
    for item in assessments:
        clean = (
            item.passed
            and not item.wrong_price
            and not item.personal_data_leak
            and not item.missed_mandatory_handoff
        )
        if clean:
            continue
        lines.append(
            f"- {item.case_id}: {item.tool_comment} {item.values_comment} "
            f"{item.handoff_comment} {item.contact_comment} {item.price_comment}"
        )
    return "\n".join(lines)


def main() -> int:
    try:
        outcome, assessments, location = run_experiment()
    except SystemExit as stopped:
        message = stopped.code
        if isinstance(message, str):
            print(message)
            return 2
        return int(message) if isinstance(message, int) else 2
    print(format_report(outcome, assessments, location))
    return 0 if outcome.ok else 1


__all__ = ["format_report", "main", "run_experiment"]
