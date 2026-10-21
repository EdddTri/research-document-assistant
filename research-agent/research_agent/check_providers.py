"""Check configured chat/vision providers and local Ollama embeddings."""
import argparse
import io
import time
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel
from research_core.config import settings
from .providers import PROVIDER_LABELS, embed, structured
from .vision import VisionError, recognize


class Check(BaseModel):
    ok: bool


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    only = parser.add_mutually_exclusive_group()
    only.add_argument("--embeddings-only", action="store_true", help="Check local embeddings only")
    only.add_argument("--vision-only", action="store_true", help="Check configured vision on a generated image (uses the API)")
    only.add_argument("--chat-only", action="store_true", help="Check configured chat JSON without embeddings or vision")
    args = parser.parse_args()
    cfg = settings()
    vision_label = PROVIDER_LABELS[cfg.vision_provider]
    if args.chat_only:
        check_chat()
        return
    if not args.vision_only:
        vectors = embed(["Research service preflight", "A second passage checks batched embeddings."])
        assert len(vectors) == 2 and all(len(v) == cfg.embedding_dimensions for v in vectors)
        print(f"Ollama {cfg.embedding_model}: two {cfg.embedding_dimensions}-dimensional embeddings verified.")
    if args.embeddings_only:
        return
    image = Image.new("RGB", (1024, 768), "white")
    ImageDraw.Draw(image).text((60, 100), "Research test\nParticipants: 42", fill="black", font=ImageFont.load_default(size=40))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    start = time.monotonic()
    try:
        layout = recognize(buffer.getvalue())
    except VisionError as exc:
        raise SystemExit(f"{vision_label} vision preflight failed: {exc}") from None
    if "42" not in layout.transcription:
        raise SystemExit(f"{vision_label} vision ran but did not recover the expected test number 42")
    print(f"{vision_label} {cfg.vision_model}: image transcription verified in {time.monotonic() - start:.1f}s.")
    if args.vision_only:
        return
    check_chat()


def check_chat():
    cfg = settings()
    result = structured(Check, "Return JSON with ok true.", "Test the configured answer model.")
    if not result.ok:
        raise SystemExit("Chat provider did not return the expected test result")
    print(f"{PROVIDER_LABELS[cfg.answer_provider]} {cfg.answer_model}: answer JSON verified.")


if __name__ == "__main__":
    main()
