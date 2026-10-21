"""Benchmark existing public PDFs through the worker, with isolated temporary DB rows.

No Redis dispatch or HTTP upload is measured. Existing extraction cache is retained.
The original documents stay searchable and unchanged. Temporary crops/rows are removed.
"""
import argparse
import json
import shutil
from pathlib import Path
from time import perf_counter
from langsmith import Client, trace, tracing_context
from sqlalchemy import delete, func, select, text
from research_core.config import settings
from research_core.db import session_factory
from research_core.models import Chunk, Content, Document, Job, uid
from research_core.storage import storage_path
from research_agent.worker import ingest
from .public_suite import PAPERS


def benchmark(document_id):
    identifier, job_id = uid(), uid()
    with session_factory("migration")() as db:
        source = db.get(Document, document_id)
        if not source or source.status != "ready" or source.sha256 not in {v[0] for v in PAPERS.values()}:
            raise ValueError("Choose a ready document from the verified public suite")
        version = db.scalar(select(func.max(Document.version)).where(Document.sha256 == source.sha256)) + 1
        doc = Document(id=identifier, title="Evaluation temporary copy: " + source.title,
            sha256=source.sha256, version=version, page_count=source.page_count,
            storage_key=source.storage_key, status="queued", reserved=False)
        db.add(doc)
        db.flush()
        db.add(Job(id=job_id, document_id=identifier))
        db.commit()
    try:
        with trace("ingestion-benchmark", inputs={"source_document_id": document_id, "pages": source.page_count},
                   metadata={"cache_policy": "reuse-existing", "redis_dispatch": False, "temporary_copy": True}) as span:
            result = ingest.run(job_id)
            with session_factory("migration")() as db:
                result["persisted_chunks"] = db.scalar(select(func.count()).select_from(Chunk).where(Chunk.document_id == identifier))
                result["collection_chunk_relation_bytes"] = db.scalar(text("SELECT pg_total_relation_size('knowledge.document_chunks')"))
                result["collection_chunk_indexes_bytes"] = db.scalar(text("SELECT pg_indexes_size('knowledge.document_chunks')"))
            result.update(source_document_id=document_id, title=source.title,
                          cache_policy="reuse-existing", upload_seconds=None, queue_seconds=None,
                          size_scope="Relation/index bytes are for the entire collection including the temporary copy; vector_payload_bytes is per document")
            span.end(outputs=result)
        return result
    finally:
        with session_factory("migration")() as db:
            for model in (Chunk, Content, Job):
                db.execute(delete(model).where(model.document_id == identifier))
            db.execute(delete(Document).where(Document.id == identifier))
            db.commit()
        # Only remove the newly allocated UUID's crops, never the source PDF.
        base = storage_path("crops").resolve()
        crop = (base / identifier).resolve()
        if crop.parent != base:
            raise ValueError("Unsafe temporary crop path")
        if crop.exists():
            shutil.rmtree(crop)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not settings().trace_document_content:
        parser.error("TRACE_DOCUMENT_CONTENT=true is required for this traced benchmark")
    if args.output.exists():
        parser.error("Choose a new output filename")
    results = []
    with tracing_context(enabled=True):
        for identifier in args.document_id:
            result = benchmark(identifier)
            results.append(result)
            print(json.dumps(result), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    Client().flush()


if __name__ == "__main__":
    main()
