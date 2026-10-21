from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Citation(Contract):
    evidence_id: str
    document_id: str
    title: str
    version: int
    page: int = Field(ge=1)
    bbox: list[float] | None = None
    source_url: str


class Block(Contract):
    type: Literal["text", "table", "image", "citation", "limitation"]
    text: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    content_id: str | None = None
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str | None]] = Field(default_factory=list)
    image_url: str | None = None


class ModelUsage(Contract):
    model: str
    service_tier: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)


class QueryUsage(Contract):
    calls: list[ModelUsage] = Field(default_factory=list)
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    usage_complete: bool
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    known_cost_usd: float = Field(default=0, ge=0)
    pricing_version: str


class Answer(Contract):
    schema_version: Literal["1"] = "1"
    blocks: list[Block]
    citations: list[Citation]
    answerability: Literal["full", "partial", "none"]
    retrieved_ids: list[str] = Field(default_factory=list)
    usage: QueryUsage | None = None


class AnswerRequest(Contract):
    schema_version: Literal["1"] = "1"
    question: str = Field(min_length=1, max_length=4000)
    document_ids: list[str] = Field(max_length=20)
    history: list[dict] = Field(default_factory=list, max_length=12)


class Region(Contract):
    kind: Literal["figure", "table"]
    bbox: list[float] = Field(min_length=4, max_length=4)
    caption: str = ""
    description: str = ""
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str | None]] = Field(default_factory=list)


class Layout(Contract):
    transcription: str = ""
    regions: list[Region] = Field(default_factory=list, max_length=20)


class Claim(Contract):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class Draft(Contract):
    answerability: Literal["full", "partial", "none"]
    claims: list[Claim] = Field(default_factory=list, max_length=16)
    display_ids: list[str] = Field(default_factory=list, max_length=6)
    limitations: list[str] = Field(default_factory=list, max_length=8)
    rewrite: str = ""

    @model_validator(mode="after")
    def check_claims(self):
        if self.answerability == "full" and not self.claims:
            raise ValueError("A full answer needs claims.")
        return self
