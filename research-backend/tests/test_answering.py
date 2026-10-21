import pytest
from research_core.contracts import AnswerRequest, Claim, Draft
from research_agent import answering
from research_agent.extraction import valid_bbox
from evaluation.run import metrics


def evidence():
    return [{"id": "e1", "document_id": "d1", "title": "Test paper", "version": 1, "page": 2,
             "kind": "text", "text": "The study included 42 participants.", "caption": "", "description": "",
             "data": {}, "bbox": None, "crop_key": None}]


def test_valid_claim_and_verified_source_link():
    result = answering.validate_answer(Draft(answerability="full",
        claims=[Claim(text="The study included 42 participants.", evidence_ids=["e1"])]), evidence())
    assert result.answerability == "full"
    assert result.citations[0].page == 2
    assert result.citations[0].source_url == "/documents/d1/source#page=2"


@pytest.mark.parametrize("count", ["110M", "110 M", "110\u202fM", "110 million"])
def test_parameter_counts_allow_equivalent_magnitude_spacing(count):
    item = evidence()[0]
    item["text"] = "BERT BASE (L=12, H=768, A=12, Total Parameters=110M)."
    result = answering.validate_answer(Draft(answerability="full", claims=[
        Claim(text=f"BERT BASE has 12 layers and {count} parameters.", evidence_ids=["e1"])]), [item])
    assert result.answerability == "full"


@pytest.mark.parametrize("count", ["110", "110B", "110 billion", "999M"])
def test_parameter_counts_reject_wrong_magnitude_or_value(count):
    item = evidence()[0]
    item["text"] = "The model has 110M parameters."
    result = answering.validate_answer(Draft(answerability="full", claims=[
        Claim(text=f"The model has {count} parameters.", evidence_ids=["e1"])]), [item])
    assert result.answerability == "none"


@pytest.mark.parametrize("claim", [
    Claim(text="The study had 999 participants.", evidence_ids=["e1"]),
    Claim(text="The study had 42 participants.", evidence_ids=["invented-id"]),
    Claim(text="An unsupported assertion.", evidence_ids=[]),
])
def test_invalid_citations_and_fabricated_values_are_removed(claim):
    result = answering.validate_answer(Draft(answerability="full", claims=[claim]), evidence())
    assert result.answerability == "none"
    assert not any(b.type == "text" for b in result.blocks)


def test_workflow_rewrites_only_once(monkeypatch):
    calls = []
    monkeypatch.setattr(answering, "retrieve", lambda query, ids: calls.append(query) or evidence())
    monkeypatch.setattr(answering, "structured", lambda *args, **kwargs:
                        Draft(answerability="none", rewrite="try again", limitations=["Missing evidence"]))
    result = answering.answer(AnswerRequest(question="What is unknown?", document_ids=["d1"]))
    assert len(calls) == 2
    assert result.answerability == "none"


def test_table_cells_render_from_stored_data():
    item = evidence()[0]
    item.update(kind="table", data={"headers": ["Sample"], "rows": [["42"], [None]]})
    result = answering.validate_answer(Draft(answerability="partial", display_ids=["e1"]), [item])
    table = next(block for block in result.blocks if block.type == "table")
    assert table.rows == [["42"], [None]]


def test_invalid_regions_fall_back_to_full_page():
    assert valid_bbox([0, 0, 1, 1])
    for box in ([0, 0, 2, 1], [1, 0, 0, 1], [0, 0, float("nan"), 1]):
        assert not valid_bbox(box)


def test_evaluation_reports_counts_and_rank():
    output = {"retrieved_ids": ["other", "e1"], "citations": [{"document_id": "d1", "page": 2}],
              "blocks": [{"type": "text", "evidence_ids": ["e1"]}], "answerability": "full"}
    expected = {"evidence_ids": ["e1", "e2"], "sources": [{"document_id": "d1", "page": 2}], "answerability": "full"}
    result = metrics(output, expected)
    assert result["retrieval_recall"] == .5
    assert result["mrr"] == .5
    assert result["supporting_sources_expected"] == 2


def test_retrieval_keeps_matched_text_chunk_instead_of_expanding_entire_page(database, monkeypatch):
    from research_core.db import session_factory
    from research_core.models import Document, Content, Chunk
    from research_core.config import settings
    relevant = "The model uses 8 attention heads."
    irrelevant = "Unrelated experiment had 999 participants. " * 200
    with session_factory()() as db:
        doc = Document(title="Test", sha256="a" * 64, page_count=1, status="ready", storage_key="unused.pdf")
        db.add(doc); db.flush()
        item = Content(document_id=doc.id, kind="text", page=1, text=irrelevant + relevant,
                       page_width=612, page_height=792, method="pdfium", processing_version="test")
        db.add(item); db.flush()
        for value, vector in [(relevant, [1.] + [0.] * 1023), (irrelevant[:1800], [0., 1.] + [0.] * 1022)]:
            db.add(Chunk(document_id=doc.id, content_id=item.id, text=value, embedding=vector,
                         embedding_model=settings().embedding_model, processing_version="test"))
        db.commit()
        doc_id = doc.id
    monkeypatch.setattr(answering, "embed", lambda _: [[1.] + [0.] * 1023])
    result = answering.retrieve("attention heads", [doc_id])
    assert len(result) == 1 and result[0]["text"] == relevant
    assert result[0]["page"] == 1 and result[0]["document_id"] == doc_id
    rejected = answering.validate_answer(Draft(answerability="full", claims=[
        Claim(text="There were 999 participants.", evidence_ids=[result[0]["id"]])]), result)
    assert rejected.answerability == "none"
