"""Publish a completed pytest JUnit report to the configured LangSmith tracing project."""
import argparse
import os
from pathlib import Path
import xml.etree.ElementTree as ET
from langsmith import Client, trace, tracing_context
from research_core.config import settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("junit", type=Path)
    args = parser.parse_args()
    if not settings().trace_document_content:
        parser.error("TRACE_DOCUMENT_CONTENT=true is required to publish the report")
    root = ET.parse(args.junit).getroot()
    cases = []
    for case in root.iter("testcase"):
        status = next((name for name in ("failure", "error", "skipped") if case.find(name) is not None), "passed")
        cases.append({"name": case.get("classname", "") + "::" + case.get("name", ""),
                      "status": status, "duration_seconds": float(case.get("time", "0"))})
    if not cases:
        raise ValueError("The JUnit report contains no test cases")
    counts = {status: sum(case["status"] == status for case in cases) for status in ("passed", "failure", "error", "skipped")}
    client = Client()
    with tracing_context(enabled=True):
        with trace("automated-test-report", client=client,
                   inputs={"report_filename": args.junit.name, "format": "pytest-junit"},
                   metadata={"recorded_after_execution": True}) as run:
            run.end(outputs={"counts": counts, "tests": cases,
                             "note": "Recorded from completed pytest output; span latency is report publication time, not test execution time."})
        client.flush()
        project = client.read_project(project_name=os.getenv("LANGSMITH_PROJECT", "default"))
        for key, value in counts.items():
            client.create_feedback(run_id=run.id, session_id=project.id, key=f"tests_{key}", score=value)
    print(f"Published {len(cases)} tests: {counts}")


if __name__ == "__main__":
    main()
