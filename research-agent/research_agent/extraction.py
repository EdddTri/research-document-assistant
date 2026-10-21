import hashlib
import io
import json
import math
import re

import pdfplumber
import pypdfium2 as pdfium

from research_core.config import settings
from research_core.contracts import Layout
from research_core.db import session_factory
from research_core.models import Chunk, Content, ExtractionCache, uid
from research_core.storage import storage_path, write_private
from .providers import PROVIDER_LABELS, embed
from .vision import VISION_VERSION, VisionError, recognize

PROCESSING_VERSION = "layout-10-openai-adaptive-crops/chunks-2"
# Separate OpenAI high-detail extraction from historical provider caches.
LAYOUT_CACHE_VERSION = "layout-10-openai-region/chunks-2"


def valid_bbox(box):
    return len(box) == 4 and all(math.isfinite(x) and 0 <= x <= 1 for x in box) and box[2] > box[0] and box[3] > box[1]


def overlap(a, b):
    area = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return area / smaller if smaller > 0 else 0


def png_bytes(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def layout_for(image, native_text):
    cfg = settings()
    identity = f"openai/{cfg.vision_model}/{VISION_VERSION}/{LAYOUT_CACHE_VERSION}/{cfg.vision_max_tokens}/{cfg.vision_reasoning_effort}"
    key = hashlib.sha256(image + identity.encode()).hexdigest()
    with session_factory("worker")() as db:
        cached = db.get(ExtractionCache, key)
        if cached:
            return Layout.model_validate(cached.data)
    layout = recognize(image)
    with session_factory("worker")() as db:
        # A single worker is configured; tolerate cached results on a retried task.
        if not db.get(ExtractionCache, key):
            db.add(ExtractionCache(key=key, data=layout.model_dump()))
            db.commit()
    return layout


def text_chunks(text, size=1800, overlap_size=200):
    text = text.strip()
    return [text[i:i + size] for i in range(0, len(text), size - overlap_size)] if text else []


def usable_text(text):
    # Short titles and page numbers are valid native text, not an OCR failure.
    return any(c.isalnum() for c in text) and text.count("\ufffd") / max(1, len(text)) < .02


def complete_table_borders(page, table):
    """Recover outer columns when ruled tables omit their left/right borders."""
    x0, y0, x1, y1 = table.bbox
    rules = [line for line in getattr(page, "lines", [])
             if abs(line["top"] - line["bottom"]) < 1 and y0 - 2 <= line["top"] <= y1 + 2
             and line["x0"] <= x0 + 2 and line["x1"] >= x1 - 2]
    for rule in rules:
        left, right = rule["x0"], rule["x1"]
        matching = [line for line in rules if abs(line["x0"] - left) < 2 and abs(line["x1"] - right) < 2]
        if len(matching) < 2 or (left >= x0 - 2 and right <= x1 + 2):
            continue
        cropped = page.crop((max(0, left - 1), max(0, y0 - 1),
                             min(page.width, right + 1), min(page.height, y1 + 1)))
        expanded = cropped.find_tables({"explicit_vertical_lines": [left, right]})
        for candidate in expanded:
            if abs(candidate.bbox[0] - left) < 2 and abs(candidate.bbox[2] - right) < 2:
                return candidate
    return table


def native_tables(page, width, height):
    tables = []
    for table in page.find_tables():
        rows = table.extract()
        if not rows or len(rows) < 2:
            continue
        clean = [[str(cell) if cell is not None else None for cell in row] for row in rows]
        populated = sum(bool(cell and cell.strip()) for row in clean for cell in row)
        if populated < 2:
            continue
        # Crossing attention/plot strokes can look like dozens of table columns
        # to pdfplumber. Do not let a mostly empty pseudo-grid suppress vision.
        columns = max(map(len, clean))
        if columns > 12 and populated / max(1, sum(map(len, clean))) < .5:
            continue
        completed = complete_table_borders(page, table)
        if completed is not table:
            table = completed
            clean = [[str(cell) if cell is not None else None for cell in row] for row in table.extract()]
        box = [table.bbox[0] / width, table.bbox[1] / height, table.bbox[2] / width, table.bbox[3] / height]
        if valid_bbox(box) and not any(overlap(box, existing) > .95 for existing, _ in tables):
            tables.append((box, {"headers": [v or "" for v in clean[0]], "rows": clean[1:], "merged_cells": None}))
    return tables


def merge_boxes(boxes, gap_x=0, gap_y=0):
    """Connected components of touching/nearby boxes, including transitive joins."""
    groups = []
    for box in boxes:
        count = 1
        remaining = list(groups)
        groups = []
        while remaining:
            other, other_count = remaining.pop(0)
            if (box[0] <= other[2] + gap_x and other[0] <= box[2] + gap_x
                    and box[1] <= other[3] + gap_y and other[1] <= box[3] + gap_y):
                box = [min(box[0], other[0]), min(box[1], other[1]),
                       max(box[2], other[2]), max(box[3], other[3])]
                count += other_count
                remaining.extend(groups)
                groups = []
            else:
                groups.append((other, other_count))
        groups.append((box, count))
    return groups


def vision_targets(page, native, table_boxes):
    """Return (reason, normalized page box) targets using only local PDF objects.

    Readable pages get padded visual crops. Unreadable/scanned pages get a
    full-page target so OCR can recover text outside detected image objects too.
    """
    width, height = page.width, page.height
    images = [obj for obj in page.images
              if abs(obj["x1"] - obj["x0"]) >= 12 and abs(obj["bottom"] - obj["top"]) >= 12]
    if not usable_text(native) and (native.strip() or page.chars or images):
        return [("scanned or unreadable text", [0., 0., 1., 1.])]
    candidates = []
    for obj in images:
        box = [max(0, obj["x0"]) / width, max(0, obj["top"]) / height,
               min(width, obj["x1"]) / width, min(height, obj["bottom"]) / height]
        if valid_bbox(box):
            candidates.append(box)
    drawings = []
    for obj in [*page.curves, *page.rects, *page.lines]:
        # Expand line strokes slightly so table containment works for zero-area lines.
        box = [max(0, obj["x0"] - 1) / width, max(0, obj["top"] - 1) / height,
               min(width, obj["x1"] + 1) / width, min(height, obj["bottom"] + 1) / height]
        if not valid_bbox(box):
            continue
        if any(box[0] >= table[0] - 2 / width and box[1] >= table[1] - 2 / height
               and box[2] <= table[2] + 2 / width and box[3] <= table[3] + 2 / height
               for table in table_boxes):
            continue
        dx, dy = obj["x1"] - obj["x0"], obj["bottom"] - obj["top"]
        if dx * dy > width * height * .9:  # page background/border
            continue
        drawings.append(box)
    for box, _ in merge_boxes(drawings, 12 / width, 12 / height):
        dx, dy = (box[2] - box[0]) * width, (box[3] - box[1]) * height
        if dx >= 12 and dy >= 12:
            candidates.append(box)
    if candidates and not usable_text(native):
        return [("scanned or unreadable text", [0., 0., 1., 1.])]
    # Padding includes nearby labels/captions; merge intersecting crops so a
    # figure made from multiple images/strokes is not sent several times.
    padded = [[max(0., b[0] - 12 / width), max(0., b[1] - 24 / height),
               min(1., b[2] + 12 / width), min(1., b[3] + 24 / height)] for b in candidates]
    reason = "embedded image / vector graphic" if images else "vector graphic or unresolved table"
    return [(reason, box) for box, _ in sorted(merge_boxes(padded), key=lambda item: (item[0][1], item[0][0]))]


def crop_target(image, box):
    # Map OCR coordinates using the actual integer pixel crop, not its rounded
    # PDF bounds. This keeps saved figure crops and citation boxes aligned.
    # Normalized integer bounds can round to 6.999999999999999 on a round-trip.
    # Remove that floating-point noise before floor/ceil to avoid growing a crop.
    pixels = (max(0, math.floor(round(box[0] * image.width, 8))), max(0, math.floor(round(box[1] * image.height, 8))),
              min(image.width, math.ceil(round(box[2] * image.width, 8))), min(image.height, math.ceil(round(box[3] * image.height, 8))))
    actual = [pixels[0] / image.width, pixels[1] / image.height,
              pixels[2] / image.width, pixels[3] / image.height]
    return image.crop(pixels), actual


def page_box(local, target):
    # OCR boxes are relative to its input crop; stored evidence uses page space.
    if not valid_bbox(local):
        return list(target)
    x0, y0, x1, y1 = target
    return [x0 + local[0] * (x1 - x0), y0 + local[1] * (y1 - y0),
            x0 + local[2] * (x1 - x0), y0 + local[3] * (y1 - y0)]


def split_boxes(image):
    """Bisect the longer eligible axis near a light seam, with bounded overlap."""
    cfg = settings()
    axis = 0 if image.width >= image.height else 1
    length = image.size[axis]
    if length < 2 * cfg.vision_split_min_side:
        return []
    gray = image.convert("L")
    low = max(cfg.vision_split_min_side, int(length * .4))
    high = min(length - cfg.vision_split_min_side, int(length * .6))
    def score(position):
        strip = gray.crop((position, 0, position + 1, image.height) if axis == 0
                          else (0, position, image.width, position + 1))
        histogram = strip.histogram()
        ink = sum(histogram[:235]) / (image.height if axis == 0 else image.width)
        return ink + .05 * abs(position / length - .5)
    seam = min(range(low, high + 1), key=score)
    padding = round(length * cfg.vision_split_overlap / 2)
    first, second = [0., 0., 1., 1.], [0., 0., 1., 1.]
    first[axis + 2], second[axis] = (seam + padding) / length, (seam - padding) / length
    return [first, second]


def adaptive_layouts(image, native_text, progress, depth=0, target=None):
    """Return validated leaf layouts in original-crop coordinates; never publish partial success."""
    target = target or [0., 0., 1., 1.]
    try:
        return [(layout_for(png_bytes(image), native_text), target)]
    except VisionError as exc:
        if exc.code != "truncated":
            raise
        boxes = split_boxes(image) if depth < settings().vision_split_max_depth else []
        if not boxes:
            raise VisionError("split_exhausted") from None
    progress(f"vision output truncated; splitting depth {depth + 1}/{settings().vision_split_max_depth}")
    leaves = []
    for index, box in enumerate(boxes, 1):
        cropped, actual = crop_target(image, box)
        progress(f"vision split depth {depth + 1}: section {index}/2 ({cropped.width}x{cropped.height})")
        leaves.extend(adaptive_layouts(cropped, native_text, progress, depth + 1, page_box(actual, target)))
    return leaves


def deduplicate_transcriptions(leaves):
    """Remove matching substantive lines only where source tiles overlap; retain short labels/numbers."""
    seen, output = [], []
    for layout, box in leaves:
        text = layout.transcription.strip()
        # Also handle a repeated boundary paragraph whose line wrapping differs.
        # Exact word matches avoid fuzzy removal of similar but distinct results.
        for previous_box, previous_text in output:
            if overlap(box, previous_box) <= 0:
                continue
            words = list(re.finditer(r"\S+", text))
            before = previous_text.casefold().split()
            current = [word.group().casefold() for word in words]
            for count in range(min(len(before), len(current)), 7, -1):
                substantive = sum(any(char.isalpha() for char in word) for word in current[:count]) >= 3
                if substantive and before[-count:] == current[:count]:
                    text = text[words[count - 1].end():].lstrip()
                    break
        kept, pending = [], []
        for line in text.splitlines():
            normalized = " ".join(line.split()).casefold()
            substantive = sum(any(char.isalpha() for char in word) for word in normalized.split()) >= 3
            if len(normalized) >= 12 and substantive:
                if any(normalized == previous and overlap(box, previous_box) > 0 for previous, previous_box in seen):
                    continue
                pending.append((normalized, box))
            kept.append(line)
        seen.extend(pending)
        output.append((box, "\n".join(kept).strip()))
    return output


def extract(document, attempt_token, progress):
    contents = []
    path = storage_path(document.storage_key)
    with pdfium.PdfDocument(path) as pdf, pdfplumber.open(path) as tables_pdf:
        for page_index in range(len(pdf)):
            progress(f"extracting page {page_index + 1}/{len(pdf)}")
            page = pdf[page_index]
            try:
                width, height = page.get_size()
                if width <= 0 or height <= 0:
                    raise ValueError("Invalid page dimensions")
                text_page = page.get_textpage()
                try:
                    native = text_page.get_text_range()
                finally:
                    text_page.close()
                bitmap = page.render(scale=min(2, 1600 / max(width, height)))
                try:
                    image = bitmap.to_pil().copy().convert("RGB")
                finally:
                    bitmap.close()
            finally:
                page.close()
            native_page = tables_pdf.pages[page_index]
            tables = native_tables(native_page, width, height)
            native_boxes = [box for box, _ in tables]
            targets = vision_targets(native_page, native, native_boxes)
            regions, transcriptions, split_sources = [], [], []
            if not targets:
                progress(f"native extraction page {page_index + 1}/{len(pdf)}: vision skipped")
            for target_index, (reason, box) in enumerate(targets, 1):
                scope = "full page" if box == [0., 0., 1., 1.] else f"crop {target_index}/{len(targets)}"
                stage = f"{PROVIDER_LABELS[settings().vision_provider]} vision page {page_index + 1}/{len(pdf)} {scope}: {reason}"
                progress(stage)
                cropped, target = crop_target(image, box)
                leaves = adaptive_layouts(cropped, native, lambda detail: progress(f"{stage}; {detail}"))
                split = len(leaves) > 1
                if split:
                    split_sources.append(target)
                for layout, tile in leaves:
                    tile_page = page_box(tile, target)
                    for region in layout.regions:
                        mapped = region.model_copy(update={"bbox": page_box(region.bbox, tile_page)}, deep=True)
                        # Do not merge fragments into a fabricated complete chart/table.
                        if split:
                            mapped.description = "Partial view from split extraction; relationships outside this section are unverified. " + mapped.description
                        if not any(mapped.kind == existing.kind and overlap(mapped.bbox, existing.bbox) > .8
                                   and mapped.model_dump(exclude={"bbox"}) == existing.model_dump(exclude={"bbox"})
                                   for existing in regions):
                            regions.append(mapped)
                transcriptions.extend((page_box(tile, target), text) for tile, text in deduplicate_transcriptions(leaves) if text)
            usable = usable_text(native)
            text = native if usable else "\n\n".join(value for _, value in transcriptions) or native

            def make(kind, text="", box=None, caption="", description="", data=None, method=None):
                content_id = uid()
                reliable = box is not None and valid_bbox(box)
                original = next((source for source in split_sources if reliable and method != "pdfplumber" and
                                 box[0] >= source[0] - 1e-6 and box[1] >= source[1] - 1e-6 and
                                 box[2] <= source[2] + 1e-6 and box[3] <= source[3] + 1e-6), None)
                reference = original or box
                crop = crop_target(image, reference)[0] if reliable else image
                data = dict(data or {})
                if original:
                    data.update(split_extraction=True, original_region_bbox=original,
                                evidence_region_bbox=box)
                crop_key = f"crops/{document.id}/{attempt_token}/{content_id}.png"
                write_private(crop_key, png_bytes(crop))
                item = Content(id=content_id, document_id=document.id, kind=kind, page=page_index + 1,
                    text=text, caption=caption, description=description, data=data or {},
                    bbox=[box[0] * width, box[1] * height, box[2] * width, box[3] * height] if reliable else None,
                    page_width=width, page_height=height, crop_key=crop_key, method=method or f"{settings().vision_provider}-vision",
                    processing_version=PROCESSING_VERSION)
                contents.append(item)
                return item

            split_scan = not usable and bool(split_sources)
            if text.strip() and not split_scan:
                make("text", text=text, method="pdfium" if usable else f"{settings().vision_provider}-vision-transcription")
            if usable or split_scan:
                # A crop may yield only text (no figure/table block). Keep that
                # evidence searchable even though the page has a native text layer.
                for box, transcription in transcriptions:
                    make("text", text=transcription, box=box, method=f"{settings().vision_provider}-vision-transcription")
            for box, data in tables:
                match = next((region for region in regions
                              if region.kind == "table" and overlap(region.bbox, box) > .7), None)
                make("table", text=json.dumps(data, ensure_ascii=False), box=box, data=data,
                     caption=match.caption if match else "", description=match.description if match else "",
                     method="pdfplumber")
            for region in regions:
                if region.kind == "table" and any(overlap(region.bbox, box) > .7 for box in native_boxes):
                    continue
                data = {"headers": region.headers, "rows": region.rows, "merged_cells": None} if region.kind == "table" else {}
                searchable = "\n".join([region.caption, region.description, json.dumps(data) if data else ""])
                make(region.kind, text=searchable, box=region.bbox, caption=region.caption,
                     description=region.description, data=data)
    progress("embedding")
    chunks = []
    for item in contents:
        if item.kind == "table":
            rows = item.data.get("rows", [])
            pieces = [json.dumps({"caption": item.caption, "headers": item.data.get("headers", []),
                                  "rows": rows[i:i + 12]}, ensure_ascii=False) for i in range(0, len(rows), 12)]
            # Complex or damaged OCR tables retain their raw transcription for retrieval.
            pieces = [part for piece in pieces for part in text_chunks(piece)] if rows else text_chunks(item.text)
        else:
            pieces = text_chunks(item.text)
        for piece in pieces:
            chunks.append(Chunk(id=uid(), document_id=document.id, content_id=item.id, text=piece,
                                embedding_model=settings().embedding_model, processing_version=PROCESSING_VERSION))
    if not chunks:
        raise ValueError("No usable evidence could be extracted")
    vectors = embed([chunk.text for chunk in chunks])
    for chunk, vector in zip(chunks, vectors, strict=True):
        chunk.embedding = vector
    return contents, chunks
