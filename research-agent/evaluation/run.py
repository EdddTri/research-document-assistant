"""Run live, reproducible RAG experiments locally or in LangSmith (never replay answers)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from threading import Lock
from time import perf_counter
from concurrent.futures import ThreadPoolExecutor

from langsmith import Client, get_current_run_tree, trace, tracing_context
from research_core.config import settings
from research_core.contracts import AnswerRequest
from research_agent.answering import answer
from research_agent.diagnostics import capture_diagnostics
from .evaluators import RUBRIC_VERSION, evaluate_output, metrics, summarize
from .langsmith_resources import link_agent_resources


def target(inputs):
    started = perf_counter()
    with capture_diagnostics() as diagnostic:
        try:
            output = answer(AnswerRequest.model_validate(inputs)).model_dump()
        except Exception as exc:
            output = {"blocks": [], "citations": [], "retrieved_ids": [], "answerability": "none",
                      "error": type(exc).__name__, "timed_out": "timeout" in type(exc).__name__.lower()}
            run = get_current_run_tree()
            if run is not None:
                run.error = type(exc).__name__
        output.update(latency_seconds=perf_counter() - started,
                      stages_seconds=diagnostic.stages, evaluation_evidence=diagnostic.evidence)
        return output


def load_cases(path, split):
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    cases = [case for case in cases if case["split"] == split]
    if not cases or any(not case.get("manually_verified") for case in cases):
        raise ValueError("Supply nonempty, manually verified cases for the chosen split")
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Case IDs must be unique")
    if len({json.dumps(case["inputs"], sort_keys=True) for case in cases}) != len(cases):
        raise ValueError("Each case must have distinct inputs")
    for case in cases:
        AnswerRequest.model_validate(case["inputs"])
        if case["expected"].get("answerability") not in {"full", "partial", "none"}:
            raise ValueError("Every reference needs answerability")
    return cases


def verify_feedback(client, rows):
    """Check each row separately: bulk offset pagination can miss tied timestamps."""
    def check(row):
        remote = list(client.list_feedback(run_ids=[row["run_id"]], limit=100))
        expected = {item["key"] for item in row["feedback"]}
        actual = {item.key for item in remote}
        return {"case_id": row["case_id"], "feedback_count": len(remote), "missing": sorted(expected - actual)}
    with ThreadPoolExecutor(max_workers=4) as pool:
        checked = list(pool.map(check, rows))
    return {"rows_checked": len(checked), "complete": all(not row["missing"] for row in checked),
            "rows": checked}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path)
    parser.add_argument("--split", choices=["development", "held-out"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--langsmith", action="store_true")
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--max-concurrency", type=int, default=1)
    parser.add_argument("--experiment-prefix", default="research-quality-security")
    args = parser.parse_args()
    cfg = settings()
    if args.langsmith and not cfg.trace_document_content:
        parser.error("LangSmith export requires TRACE_DOCUMENT_CONTENT=true")
    if args.max_concurrency < 1:
        parser.error("max-concurrency must be positive")
    try:
        cases = load_cases(args.cases, args.split)
    except ValueError as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    journal = args.output.with_suffix(".rows.jsonl")
    if args.output.exists() or journal.exists():
        parser.error("Choose a new output filename; previous experiment reports are preserved")
    digest = hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest()
    configuration = {"answer_model": cfg.answer_model, "vision_model": cfg.vision_model,
                     "embedding_model": cfg.embedding_model, "retrieval_mode": cfg.retrieval_mode,
                     "judge_model": cfg.judge_model if args.judge else None,
                     "rubric_version": RUBRIC_VERSION, "dataset_sha256": digest,
                     "max_concurrency": args.max_concurrency, "live_execution": True,
                     "models": [f"openai:{cfg.answer_model}"],
                     "configured_project": os.getenv("LANGSMITH_PROJECT", "default")}
    by_inputs = {json.dumps(case["inputs"], sort_keys=True): case for case in cases}
    rows, lock = [], Lock()

    def evaluator(run, example):
        case = by_inputs[json.dumps(example.inputs, sort_keys=True)]
        output = run.outputs or {"error": "TargetFailed", "answerability": "none"}
        evaluation = evaluate_output(example.inputs, output, example.outputs, args.judge)
        row = {"case_id": case["id"], "category": case.get("category"), "run_id": str(run.id),
               "inputs": example.inputs, "expected": example.outputs,
               "output": output, "feedback": evaluation["results"]}
        with lock:
            rows.append(row)
            with journal.open("a", encoding="utf-8") as file:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"Evaluated {len(rows)}/{len(cases)}: {case['id']} ({output.get('latency_seconds', 0):.2f}s)", flush=True)
        return evaluation

    started = perf_counter()
    links = {}
    with tracing_context(enabled=args.langsmith):
        if args.langsmith:
            client = Client()
            dataset_name = f"research-{args.split}-{digest[:12]}"
            if client.has_dataset(dataset_name=dataset_name):
                dataset = client.read_dataset(dataset_name=dataset_name)
                examples = list(client.list_examples(dataset_id=dataset.id))
                remote = {(json.dumps(e.inputs, sort_keys=True), json.dumps(e.outputs, sort_keys=True)) for e in examples}
                local = {(json.dumps(c["inputs"], sort_keys=True), json.dumps(c["expected"], sort_keys=True)) for c in cases}
                if remote != local or len(examples) != len(cases):
                    raise ValueError("Existing dataset does not match the verified local cases")
            else:
                paper_count = len({doc_id for case in cases for doc_id in case["inputs"]["document_ids"]})
                dataset = client.create_dataset(dataset_name=dataset_name,
                    description=f"{len(cases)} source-reviewed {args.split} RAG cases across {paper_count} PDFs; "
                                f"synthetic security fixtures. {RUBRIC_VERSION}")
                client.create_examples(dataset_id=dataset.id, examples=[{
                    "inputs": case["inputs"], "outputs": case["expected"],
                    "metadata": {"case_id": case["id"], "category": case.get("category"),
                                 "split": args.split, "manually_verified": True,
                                 "reference_review": case.get("reference_review", "See local reference review record"),
                                 "source_versions": case.get("source_versions", {})},
                } for case in cases])
            results = client.evaluate(target, data=dataset.id, evaluators=[evaluator],
                experiment_prefix=args.experiment_prefix, metadata=configuration,
                description="Live RAG calls with reference quality, security and operational feedback. "
                            "Judge calls/costs are separate from query latency/cost. Development scores are not held-out accuracy.",
                max_concurrency=args.max_concurrency, blocking=True)
            results.wait()
            client.flush()
            project = client.read_project(project_id=results.experiment_id)
            links = {"experiment_name": results.experiment_name, "experiment_id": str(project.id),
                     "experiment_url": project.url, "dataset_url": dataset.url, "dataset_id": str(dataset.id)}
            links["agent_association"] = link_agent_resources(
                client, configuration["configured_project"], dataset.id, project.id)
        else:
            from types import SimpleNamespace
            from uuid import uuid4
            for case in cases:
                evaluator(SimpleNamespace(id=uuid4(), outputs=target(case["inputs"])),
                          SimpleNamespace(inputs=case["inputs"], outputs=case["expected"]))
    report = {"split": args.split, "configuration": configuration, "links": links,
              "wall_seconds": perf_counter() - started, "cases": rows, "summary": summarize(rows)}
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.langsmith:
        for key, value in report["summary"].items():
            for stat in ("mean", "count", "p50", "p95"):
                if stat in value:
                    client.create_feedback(key=f"{key}_{stat}", score=value[stat], project_id=project.id)
        client.create_feedback(key="examples_evaluated", score=len(rows), project_id=project.id)
        client.create_feedback(key="experiment_wall_seconds", score=report["wall_seconds"], project_id=project.id)
        report["remote_verification"] = verify_feedback(client, rows)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        with tracing_context(enabled=True):
            with trace("quality-security-experiment-report", project_name=configuration["configured_project"],
                       client=client, inputs={"dataset": dataset_name, "case_count": len(cases)},
                       metadata=configuration) as report_run:
                report_run.end(outputs={"links": links, "summary": report["summary"],
                                        "remote_verification": report["remote_verification"],
                                        "wall_seconds": report["wall_seconds"],
                                        "limitations": ["Development dataset", "Same-model judge is not independent verification",
                                                        "Code is reviewed, not executed", "Synthetic security tests are not a security audit",
                                                        "Query latency excludes HTTP upload/UI and judge latency"]})
        client.flush()
        if not report["remote_verification"]["complete"]:
            raise RuntimeError("Some LangSmith feedback is not visible yet. See remote_verification in the saved report.")
    print(json.dumps({"links": links, "cases": len(rows), "wall_seconds": report["wall_seconds"],
                      "summary": report["summary"]}, indent=2))


if __name__ == "__main__":
    main()
