from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Backend = Literal[
    "pipeline",
    "vlm-auto-engine",
    "hybrid-auto-engine",
    "vlm-http-client",
    "hybrid-http-client",
]
ParseMethod = Literal["auto", "ocr", "txt"]
TaskStatus = Literal["pending", "processing", "completed", "failed"]
TaskSource = Literal["web", "api"]


class OcrPlaneError(RuntimeError):
    """Raised for expected CLI/API failures."""


class ContentBlock(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    bbox: list[float] | None = None
    text: str | None = None
    text_level: float | None = None
    page_idx: float | None = None
    img_path: str | None = None
    img_url: str | None = None
    table_body: str | None = None
    list_items: list[str] | None = None


class PageSize(BaseModel):
    model_config = ConfigDict(extra="allow")

    width: float
    height: float


class TaskCreated(BaseModel):
    id: str
    status: TaskStatus | str
    message: str


class Task(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    filename: str
    original_name: str
    status: TaskStatus
    source: TaskSource | str
    backend: str
    lang: str
    result_md: str | None = None
    content_list: list[ContentBlock] | None = None
    pages: list[PageSize] | None = None
    progress: str | None = None
    error: str | None = None
    created_at: str
    completed_at: str | None = None
    file_size: float
    user_id: str | None = None


class TaskSummary(BaseModel):
    task_id: str | None
    status: str | None
    original_name: str | None = None
    backend: str | None = None
    lang: str | None = None
    file_size: float | None = None
    pages_count: int | None = None
    content_blocks_count: int | None = None
    markdown_length: int = 0
    markdown_preview: str = ""
    has_large_content: bool = False
    error: str | None = None
    created_at: str | None = None
    completed_at: str | None = None
    next_commands: list[str] = Field(default_factory=list)


class ParseOptions(BaseModel):
    file_path: Path
    filename: str | None = None
    backend: Backend = "pipeline"
    lang: str = "ch"
    parse_method: ParseMethod = "auto"
    formula_enable: bool = True
    table_enable: bool = True
    start_page_id: int | None = None
    end_page_id: int | None = None

    @field_validator("file_path")
    @classmethod
    def file_must_exist(cls, value: Path) -> Path:
        path = value.expanduser()
        if not path.exists() or not path.is_file():
            raise ValueError(f"file does not exist or is not a file: {value}")
        return path

    @field_validator("lang")
    @classmethod
    def lang_must_not_be_empty(cls, value: str) -> str:
        parts = [part.strip() for part in value.split(",") if part.strip()]
        if not parts:
            raise ValueError("lang must contain at least one language code")
        return ",".join(parts)

    @model_validator(mode="after")
    def validate_pages(self) -> ParseOptions:
        if self.start_page_id is not None and self.start_page_id < 0:
            raise ValueError("start_page_id must be >= 0")
        if self.end_page_id is not None and self.end_page_id < 0:
            raise ValueError("end_page_id must be >= 0")
        if (
            self.start_page_id is not None
            and self.end_page_id is not None
            and self.end_page_id < self.start_page_id
        ):
            raise ValueError("end_page_id must be >= start_page_id")
        return self

    @property
    def upload_name(self) -> str:
        return self.filename or self.file_path.name

    def form_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "backend": self.backend,
            "parse_method": self.parse_method,
            "formula_enable": "true" if self.formula_enable else "false",
            "table_enable": "true" if self.table_enable else "false",
            "lang_list": [part.strip() for part in self.lang.split(",") if part.strip()],
        }
        if self.start_page_id is not None:
            data["start_page_id"] = str(self.start_page_id)
        if self.end_page_id is not None:
            data["end_page_id"] = str(self.end_page_id)
        return data


class MarkdownSlice(BaseModel):
    task_id: str
    status: str | None
    offset: int
    max_length: int
    next_offset: int | None
    total_length: int
    markdown: str


class ContentBlocksPage(BaseModel):
    task_id: str
    status: str | None
    offset: int
    limit: int
    next_offset: int | None
    total_blocks: int
    blocks: list[ContentBlock]


class FullResult(BaseModel):
    summary: TaskSummary
    markdown: str
    content_blocks: list[ContentBlock]
    pages: list[PageSize]
    markdown_truncated: bool
    blocks_truncated: bool


class DryRunPlan(BaseModel):
    command: str
    method: str
    url: str
    auth: str = "Authorization: Bearer <redacted>"
    payload: dict[str, Any] = Field(default_factory=dict)
    files: list[str] = Field(default_factory=list)
    note: str = "No request was sent because --dry-run was used."
