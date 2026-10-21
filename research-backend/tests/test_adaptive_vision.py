import io

import pytest
from PIL import Image

from research_agent import extraction
from research_agent.vision import VisionError
from research_core.config import settings
from research_core.contracts import Layout, Region, Draft, Claim
from research_core.storage import storage_path
from research_agent.answering import validate_answer
from test_selective_vision import run_extraction
from test_integration import research_pdf


def test_normalized_crop_bounds_round_trip_without_extra_pixels():
    image = Image.new("RGB", (773, 1000))
    box = [7 / 773, 13 / 1000, 400 / 773, 600 / 1000]
    crop, actual = extraction.crop_target(image, box)
    assert crop.size == (393, 587) and actual == box


def test_truncation_recurses_and_maps_nested_pixels(database, monkeypatch):
    calls = []
    def vision(payload, native):
        with Image.open(io.BytesIO(payload)) as image:
            calls.append(image.size)
        if len(calls) in (1, 2):
            raise VisionError("truncated")
        return Layout(transcription="Visible source words")
    monkeypatch.setattr(extraction, "layout_for", vision)
    image = Image.new("RGB", (1200, 800), "white")
    leaves = extraction.adaptive_layouts(image, "", lambda _: None)
    assert len(calls) == 5 and len(leaves) == 3
    assert calls == [(1200, 800), (648, 800), (648, 432), (648, 432), (648, 800)]
    assert leaves[0][1] == pytest.approx([0, 0, .54, .54])
    assert leaves[1][1] == pytest.approx([0, .46, .54, 1])
    assert leaves[2][1] == pytest.approx([.46, 0, 1, 1])


@pytest.mark.parametrize("code", ["rate_limit", "authentication", "format", "connection", "quota_request_size"])
def test_other_provider_failures_do_not_split(database, monkeypatch, code):
    calls = []
    def vision(*_):
        calls.append(1)
        raise VisionError(code)
    monkeypatch.setattr(extraction, "layout_for", vision)
    with pytest.raises(VisionError) as exc:
        extraction.adaptive_layouts(Image.new("RGB", (1000, 1000)), "", lambda _: None)
    assert exc.value.code == code and len(calls) == 1


@pytest.mark.parametrize("depth,size,expected", [(0, (1000, 1000), 1), (1, (1000, 1000), 2), (3, (100, 100), 1)])
def test_exhausted_split_is_bounded_and_does_not_return_partial_data(database, monkeypatch, depth, size, expected):
    settings().vision_split_max_depth = depth
    calls = []
    def vision(*_):
        calls.append(1)
        raise VisionError("truncated")
    monkeypatch.setattr(extraction, "layout_for", vision)
    with pytest.raises(VisionError) as exc:
        extraction.adaptive_layouts(Image.new("RGB", size), "", lambda _: None)
    assert exc.value.code == "split_exhausted" and len(calls) == expected


def test_dedup_handles_line_wrapping_and_keeps_short_labels_and_distant_repetitions():
    repeated = "This experiment uses the same training set for every model."
    first = Layout(transcription="A distinct opening sentence.\n42\n" + repeated)
    second = Layout(transcription=repeated.replace("training set", "training\nset") + "\n42\nNew final sentence.")
    third = Layout(transcription=repeated)
    result = extraction.deduplicate_transcriptions([
        (first, [0, 0, 1, .54]), (second, [0, .46, 1, 1]), (third, [0, .7, 1, 1])])
    assert result[1][1] == "42\nNew final sentence."
    assert result[2][1] == repeated
    assert "42" in result[0][1]


def test_dedup_preserves_repetition_within_one_source_tile():
    text = "This repeated observation is present twice.\nThis repeated observation is present twice."
    result = extraction.deduplicate_transcriptions([(Layout(transcription=text), [0, 0, 1, 1])])
    assert result[0][1] == text


def test_dedup_keeps_numeric_sequences_that_could_be_different_table_rows():
    text = "10 20 30 40 50 60 70 80 90"
    result = extraction.deduplicate_transcriptions([
        (Layout(transcription=text), [0, 0, 1, .54]),
        (Layout(transcription=text), [0, .46, 1, 1])])
    assert [value for _, value in result] == [text, text]


@pytest.mark.parametrize("scanned", [False, True])
def test_split_evidence_preserves_original_image_and_leaf_coordinates(database, monkeypatch, scanned):
    settings().vision_split_min_side = 64
    calls = []
    def vision(payload, _):
        with Image.open(io.BytesIO(payload)) as crop:
            calls.append(crop.size)
        if len(calls) == 1:
            raise VisionError("truncated")
        return Layout(transcription="Shared substantive boundary text\n" + f"Distinct section {len(calls)}",
                      regions=[Region(kind="figure", bbox=[0, 0, 1, 1], description=f"Section {len(calls)}")])
    payload = research_pdf(text=b"" if scanned else b"Native study with 42 participants.", image=True)
    contents, chunks, progress = run_extraction(monkeypatch, payload, vision)
    figures = [item for item in contents if item.kind == "figure"]
    transcriptions = [item for item in contents if item.method.endswith("vision-transcription")]
    assert len(calls) == 3 and len(figures) == len(transcriptions) == 2
    assert sum("Shared substantive boundary text" in item.text for item in transcriptions) == 1
    for item in [*figures, *transcriptions]:
        assert item.data["split_extraction"]
        assert item.data["evidence_region_bbox"] != item.data["original_region_bbox"]
        with Image.open(storage_path(item.crop_key)) as crop:
            assert crop.size == calls[0]
        assert item.bbox is not None
    assert any("splitting depth 1" in stage for stage in progress)
    assert chunks
    if not scanned:
        assert any(item.method == "pdfium" for item in contents)


def test_split_evidence_discloses_partial_context_in_answers():
    item = {"id": "e1", "document_id": "d1", "title": "Paper", "version": 1, "page": 2,
            "kind": "text", "text": "The experiment used a shared training set.", "caption": "",
            "description": "", "data": {"split_extraction": True}, "bbox": None, "crop_key": None}
    result = validate_answer(Draft(answerability="full", claims=[
        Claim(text=item["text"], evidence_ids=["e1"])]), [item])
    assert any("relationships across sections are not verified" in (block.text or "") for block in result.blocks)


def test_coordinates_cannot_validate_invented_research_values():
    item = {"id": "e1", "document_id": "d1", "title": "Paper", "version": 1, "page": 2,
            "kind": "text", "text": "The experiment compared models.", "caption": "", "description": "",
            "data": {"split_extraction": True, "evidence_region_bbox": [0, 0, .54, 1]},
            "bbox": None, "crop_key": None}
    result = validate_answer(Draft(answerability="full", claims=[
        Claim(text="The model scored 0.54.", evidence_ids=["e1"])]), [item])
    assert result.answerability == "none"


def test_successful_left_child_does_not_hide_failed_right_child(database, monkeypatch):
    calls = []
    def vision(*_):
        calls.append(1)
        if len(calls) == 1:
            raise VisionError("truncated")
        if len(calls) == 3:
            raise VisionError("format")
        return Layout(transcription="Valid but incomplete left section")
    monkeypatch.setattr(extraction, "layout_for", vision)
    with pytest.raises(VisionError) as exc:
        extraction.adaptive_layouts(Image.new("RGB", (1000, 1000)), "", lambda _: None)
    assert exc.value.code == "format" and len(calls) == 3


def test_table_sections_stay_separate_without_invented_row_joins(database, monkeypatch):
    calls = []
    def vision(*_):
        calls.append(1)
        if len(calls) == 1:
            raise VisionError("truncated")
        return Layout(regions=[Region(kind="table", bbox=[0, 0, 1, 1], headers=["Model", "Score"],
                                      rows=[[f"Model {len(calls)}", "42"]])])
    contents, _, _ = run_extraction(monkeypatch, research_pdf(text=b"", image=True), vision)
    tables = [item for item in contents if item.kind == "table"]
    assert len(tables) == 2
    assert [item.data["rows"] for item in tables] == [[["Model 2", "42"]], [["Model 3", "42"]]]
    assert all(item.data["split_extraction"] for item in tables)
