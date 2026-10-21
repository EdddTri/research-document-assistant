from types import SimpleNamespace
import io

import pytest
from PIL import Image

from research_agent import extraction
from research_core.contracts import Layout, Region
from research_core.storage import storage_path, write_private
from test_integration import research_pdf


def run_extraction(monkeypatch, payload, vision):
    write_private("pdfs/selective.pdf", payload)
    monkeypatch.setattr(extraction, "layout_for", vision)
    monkeypatch.setattr(extraction, "embed", lambda texts: [[1.] + [0.] * 1023 for _ in texts])
    progress = []
    contents, chunks = extraction.extract(SimpleNamespace(id="selective", storage_key="pdfs/selective.pdf"),
                                          "attempt", progress.append)
    return contents, chunks, progress


@pytest.mark.parametrize("text", [b"The study included 42 participants and describes a controlled trial.", b"Introduction"])
def test_text_only_pages_never_call_vision(database, monkeypatch, text):
    def forbidden(*args):
        pytest.fail("Native text must not call vision")
    contents, chunks, progress = run_extraction(monkeypatch, research_pdf(text=text), forbidden)
    assert len(contents) == len(chunks) == 1
    assert contents[0].method == "pdfium" and contents[0].text.strip() == text.decode()
    assert any("vision skipped" in stage for stage in progress)


def test_native_table_is_extracted_before_vision_routing(database, monkeypatch):
    grid = b"""
    50 500 m 250 500 l S 50 550 m 250 550 l S 50 600 m 250 600 l S
    50 500 m 50 600 l S 150 500 m 150 600 l S 250 500 m 250 600 l S
    BT /F1 12 Tf 60 570 Td (Group) Tj ET BT /F1 12 Tf 160 570 Td (Count) Tj ET
    BT /F1 12 Tf 60 520 Td (A) Tj ET BT /F1 12 Tf 160 520 Td (42) Tj ET
    """
    def forbidden(*args):
        pytest.fail("Readable native table must not call vision")
    contents, _, _ = run_extraction(monkeypatch, research_pdf(extra_stream=grid), forbidden)
    table = next(item for item in contents if item.kind == "table")
    assert table.method == "pdfplumber"
    assert table.data["headers"] == ["Group", "Count"]
    assert table.data["rows"] == [["A", "42"]]


@pytest.mark.parametrize("payload,reason", [
    (research_pdf(image=True), "embedded image"),
    (research_pdf(extra_stream=b"100 300 m 200 300 l 150 400 l h S"), "vector graphic"),
    (research_pdf(text=b"", image=True), "scanned or unreadable text"),
])
def test_visual_and_scanned_pages_still_use_vision(database, monkeypatch, payload, reason):
    calls = []
    def vision(image, text):
        calls.append(image)
        return Layout(transcription="Scanned study has 42 participants.", regions=[
            Region(kind="figure", bbox=[.1, .3, .5, .7], description="Study diagram")])
    contents, _, progress = run_extraction(monkeypatch, payload, vision)
    assert len(calls) == 1
    assert any(reason in stage for stage in progress)
    assert any(item.kind == "figure" for item in contents)
    if reason == "scanned or unreadable text":
        assert any(item.method == "openai-vision-transcription" for item in contents)


def test_decorative_rule_does_not_trigger_vision(database, monkeypatch):
    def forbidden(*args):
        pytest.fail("A simple divider is not a visual figure")
    run_extraction(monkeypatch, research_pdf(extra_stream=b"50 650 m 550 650 l S"), forbidden)


def test_garbled_native_text_triggers_fallback():
    page = SimpleNamespace(width=612, height=792, images=[], chars=[{}], curves=[], rects=[], lines=[])
    assert extraction.vision_targets(page, "\ufffd" * 20, []) == [
        ("scanned or unreadable text", [0., 0., 1., 1.])]
    page.chars = []
    assert extraction.vision_targets(page, "", []) == []


def test_readable_page_sends_only_padded_image_crop_and_maps_evidence(database, monkeypatch):
    calls = []
    def vision(image, text):
        with Image.open(io.BytesIO(image)) as crop:
            calls.append(crop.size)
        return Layout(transcription="Image-only label: 73", regions=[
            Region(kind="figure", bbox=[.25, .25, .75, .75], caption="Figure 1")])
    contents, chunks, progress = run_extraction(monkeypatch, research_pdf(image=True), vision)
    # Original page is 1224 x 1584 pixels. The image occupies x=100..220,
    # y=392..492 PDF points; padding adds 12 horizontally and 24 vertically.
    assert calls == [(288, 296)]
    figure = next(item for item in contents if item.kind == "figure")
    assert figure.bbox == pytest.approx([124, 405, 196, 479])
    with Image.open(storage_path(figure.crop_key)) as crop:
        assert crop.size == (144, 148)
    ocr_text = next(item for item in contents if item.method == "openai-vision-transcription")
    assert ocr_text.bbox == pytest.approx([88, 368, 232, 516])
    assert any(chunk.content_id == ocr_text.id and "73" in chunk.text for chunk in chunks)
    assert any(item.method == "pdfium" and "42 participants" in item.text for item in contents)
    assert any("crop 1/1" in stage for stage in progress)


def test_separate_images_are_separate_crops_and_text_only_ocr_is_kept(database, monkeypatch):
    calls = []
    def vision(image, text):
        with Image.open(io.BytesIO(image)) as crop:
            calls.append(crop.size)
        return Layout(transcription=f"Label {len(calls)}")
    pdf = research_pdf(image=True, extra_stream=b"q 80 0 0 80 400 100 cm /Im1 Do Q")
    contents, chunks, _ = run_extraction(monkeypatch, pdf, vision)
    assert calls == [(288, 296), (208, 256)]
    ocr_texts = [item for item in contents if item.method == "openai-vision-transcription"]
    assert len(ocr_texts) == 2 and all(item.bbox is not None for item in ocr_texts)
    assert {item.text for item in ocr_texts} == {"Label 1", "Label 2"}
    assert len(chunks) == 3  # Native text plus both image transcriptions.


def test_scanned_page_sends_whole_page_and_preserves_coordinates(database, monkeypatch):
    calls = []
    def vision(image, text):
        with Image.open(io.BytesIO(image)) as crop:
            calls.append(crop.size)
        return Layout(transcription="Scanned text 42", regions=[
            Region(kind="table", bbox=[.25, .25, .75, .75], headers=["N"], rows=[["42"]])])
    contents, _, progress = run_extraction(monkeypatch, research_pdf(text=b"", image=True), vision)
    assert calls == [(1224, 1584)]
    table = next(item for item in contents if item.kind == "table")
    assert table.bbox == pytest.approx([153, 198, 459, 594])
    assert sum(item.kind == "text" for item in contents) == 1
    assert any("full page" in stage for stage in progress)


def test_vector_figures_are_grouped_locally_without_joining_distant_figures(database, monkeypatch):
    calls = []
    def vision(image, text):
        with Image.open(io.BytesIO(image)) as crop:
            calls.append(crop.size)
        return Layout(regions=[Region(kind="figure", bbox=[0, 0, 1, 1])])
    # Two separate triangles, each a vector figure, plus an intersecting stroke.
    drawing = b"100 300 m 200 300 l 150 400 l h S 120 320 m 180 320 l S 400 100 m 500 100 l 450 200 l h S"
    contents, _, _ = run_extraction(monkeypatch, research_pdf(extra_stream=drawing), vision)
    assert len(calls) == 2
    assert all(w < 300 and h < 310 for w, h in calls)
    assert len([item for item in contents if item.kind == "figure"]) == 2


def test_overlapping_raster_and_vector_targets_are_merged(database, monkeypatch):
    calls = []
    def vision(image, text):
        calls.append(image)
        return Layout(transcription="Merged figure")
    run_extraction(monkeypatch, research_pdf(image=True, extra_stream=b"100 300 m 200 300 l 150 400 l h S"), vision)
    assert len(calls) == 1


def test_edge_crop_padding_is_clamped_and_mapping_uses_actual_pixels():
    obj = {"x0": 0, "top": 0, "x1": 50, "bottom": 50}
    page = SimpleNamespace(width=612, height=792, images=[obj], chars=[], curves=[], rects=[], lines=[])
    _, target = extraction.vision_targets(page, "Native text", [])[0]
    assert target[:2] == [0, 0]
    image = Image.new("RGB", (773, 1000))
    crop, actual = extraction.crop_target(image, target)
    assert actual == [0, 0, crop.width / 773, crop.height / 1000]
    assert extraction.page_box([0, 0, 1, 1], actual) == actual
    assert extraction.page_box([-1, 0, 2, 1], actual) == actual


def test_transitive_visual_components_merge():
    groups = extraction.merge_boxes([[0, 0, .1, .1], [.2, 0, .3, .1], [.1, 0, .2, .1]])
    assert groups == [([0, 0, .3, .1], 3)]


def test_sparse_diagram_grid_is_not_mistaken_for_a_native_table():
    rows = [[""] * 54 for _ in range(6)]
    for index in range(0, 54, 2):
        rows[0][index] = rows[3][index] = "word"
    table = SimpleNamespace(bbox=(100, 200, 500, 600), extract=lambda: rows)
    graphic = {"x0": 100, "x1": 500, "top": 200, "bottom": 600}
    page = SimpleNamespace(width=612, height=792, find_tables=lambda: [table],
                           images=[], curves=[graphic], rects=[], lines=[], chars=[])
    tables = extraction.native_tables(page, 612, 792)
    assert tables == []
    assert len(extraction.vision_targets(page, "Native diagram labels", [])) == 1


def test_dense_wide_native_table_is_preserved():
    rows = [[f"Column {i}" for i in range(16)], [str(i) for i in range(16)]]
    table = SimpleNamespace(bbox=(50, 100, 550, 250), extract=lambda: rows)
    page = SimpleNamespace(find_tables=lambda: [table])
    tables = extraction.native_tables(page, 612, 792)
    assert len(tables) == 1 and tables[0][1]["rows"] == [rows[1]]


def test_native_table_with_open_outer_borders_keeps_all_columns_and_skips_vision(database, monkeypatch):
    grid = b"""
    50 500 m 350 500 l S 50 550 m 350 550 l S 50 600 m 350 600 l S
    150 500 m 150 600 l S 250 500 m 250 600 l S
    BT /F1 12 Tf 60 570 Td (Model) Tj ET BT /F1 12 Tf 160 570 Td (Training) Tj ET
    BT /F1 12 Tf 260 570 Td (Score) Tj ET BT /F1 12 Tf 60 520 Td (A) Tj ET
    BT /F1 12 Tf 160 520 Td (Supervised) Tj ET BT /F1 12 Tf 260 520 Td (42) Tj ET
    """
    def forbidden(*args):
        pytest.fail("Readable table with inferred outer borders must not call vision")
    contents, _, _ = run_extraction(monkeypatch, research_pdf(extra_stream=grid), forbidden)
    tables = [item for item in contents if item.kind == "table"]
    assert len(tables) == 1
    assert tables[0].data["headers"] == ["Model", "Training", "Score"]
    assert tables[0].data["rows"] == [["A", "Supervised", "42"]]
