from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from .models import (
    ContentBlocksPage,
    DryRunPlan,
    FullResult,
    MarkdownSlice,
    OcrPlaneError,
    ParseOptions,
    Task,
    TaskCreated,
    TaskSummary,
)

DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_POLL_INTERVAL_SECONDS = 3
DEFAULT_MARKDOWN_LENGTH = 12000
DEFAULT_BLOCK_LIMIT = 50


def read_base_url(required: bool = True) -> str:
    value = (
        os.getenv("OCRPLANE_BASE_URL")
        or os.getenv("MINERU_API_BASE_URL")
        or os.getenv("MINERU_BASE_URL")
        or ""
    ).strip()
    if not value:
        if not required:
            return "https://ocrplane.example.invalid/"
        raise OcrPlaneError("OCRPLANE_BASE_URL is not configured.")
    return value.rstrip("/") + "/"


def read_api_key(required: bool = True) -> str:
    value = (
        os.getenv("OCRPLANE_API_KEY")
        or os.getenv("MINERU_API_KEY")
        or os.getenv("API_KEY")
        or os.getenv("APIKEY")
        or ""
    ).strip()
    if not value:
        if not required:
            return "<missing-api-key>"
        raise OcrPlaneError("OCRPLANE_API_KEY is not configured.")
    return value


class OcrPlaneClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        allow_missing_auth: bool = False,
    ) -> None:
        self.base_url = (base_url or read_base_url(required=not allow_missing_auth)).rstrip("/") + "/"
        self.api_key = api_key or read_api_key(required=not allow_missing_auth)

    def url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip("/"))

    def dry_run(self, command: str, method: str, path: str, payload: dict[str, Any] | None = None) -> DryRunPlan:
        return DryRunPlan(command=command, method=method, url=self.url(path), payload=payload or {})

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        timeout = kwargs.pop("timeout", 60)
        headers = kwargs.pop("headers", {})
        headers = {**headers, "Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.request(method, self.url(path), headers=headers, **kwargs)
        if response.status_code >= 400:
            detail = self._response_detail(response)
            raise OcrPlaneError(f"OcrPlane request failed: {response.status_code} {detail}")
        if not response.content:
            return None
        return response.json()

    def parse(self, options: ParseOptions) -> TaskCreated:
        with options.file_path.open("rb") as fh:
            files = {"file": (options.upload_name, fh, "application/octet-stream")}
            data = options.form_data()
            response = self.request("POST", "/api/parse", data=data, files=files, timeout=120)
        return TaskCreated.model_validate(response)

    def parse_sync(self, options: ParseOptions) -> Task:
        with options.file_path.open("rb") as fh:
            files = {"file": (options.upload_name, fh, "application/octet-stream")}
            data = options.form_data()
            response = self.request("POST", "/api/parse/sync", data=data, files=files, timeout=None)
        return Task.model_validate(
            {
                "filename": options.upload_name,
                "original_name": options.upload_name,
                "source": "api",
                "backend": options.backend,
                "lang": options.lang,
                "progress": None,
                "error": None,
                "created_at": "",
                "completed_at": "",
                "file_size": Path(options.file_path).stat().st_size,
                "result_md": response.get("markdown"),
                "content_list": response.get("content_list"),
                **response,
            }
        )

    def get_task(self, task_id: str) -> Task:
        return Task.model_validate(self.request("GET", f"/tasks/{task_id}", timeout=60))

    def poll_task(
        self,
        task_id: str,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
        on_tick: Any | None = None,
    ) -> Task:
        deadline = time.monotonic() + timeout_seconds
        last_task: Task | None = None
        while time.monotonic() < deadline:
            task = self.get_task(task_id)
            last_task = task
            if on_tick is not None:
                on_tick(task)
            if task.status in {"completed", "failed"}:
                return task
            time.sleep(max(1, poll_interval_seconds))
        raise OcrPlaneError(f"Timed out waiting for task {task_id}. Last status: {last_task}")

    def markdown(self, task_id: str, offset: int = 0, max_length: int = DEFAULT_MARKDOWN_LENGTH) -> MarkdownSlice:
        task = self.get_task(task_id)
        markdown = task.result_md or ""
        start = max(0, offset)
        length = max(1, min(max_length, 60000))
        end = min(len(markdown), start + length)
        return MarkdownSlice(
            task_id=task_id,
            status=task.status,
            offset=start,
            max_length=length,
            next_offset=end if end < len(markdown) else None,
            total_length=len(markdown),
            markdown=markdown[start:end],
        )

    def content_blocks(
        self,
        task_id: str,
        offset: int = 0,
        limit: int = DEFAULT_BLOCK_LIMIT,
        page_idx: int | None = None,
    ) -> ContentBlocksPage:
        task = self.get_task(task_id)
        blocks = task.content_list or []
        if page_idx is not None:
            blocks = [block for block in blocks if block.page_idx == page_idx]
        start = max(0, offset)
        size = max(1, min(limit, 200))
        end = min(len(blocks), start + size)
        return ContentBlocksPage(
            task_id=task_id,
            status=task.status,
            offset=start,
            limit=size,
            next_offset=end if end < len(blocks) else None,
            total_blocks=len(blocks),
            blocks=blocks[start:end],
        )

    def full_result(
        self,
        task_id: str,
        max_markdown_length: int = DEFAULT_MARKDOWN_LENGTH,
        max_blocks: int = DEFAULT_BLOCK_LIMIT,
    ) -> FullResult:
        task = self.get_task(task_id)
        markdown = task.result_md or ""
        blocks = task.content_list or []
        markdown_limit = max(1, min(max_markdown_length, 60000))
        block_limit = max(1, min(max_blocks, 200))
        return FullResult(
            summary=task_summary(task),
            markdown=markdown[:markdown_limit],
            content_blocks=blocks[:block_limit],
            pages=task.pages or [],
            markdown_truncated=len(markdown) > markdown_limit,
            blocks_truncated=len(blocks) > block_limit,
        )

    def reprocess(self, task_id: str, payload: dict[str, Any]) -> TaskCreated:
        response = self.request("POST", f"/tasks/{task_id}/reprocess", json=payload, timeout=60)
        return TaskCreated.model_validate(response)

    def list_tasks(
        self,
        page: int = 1,
        limit: int = 20,
        source: str | None = "api",
        search: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "limit": min(max(1, limit), 100)}
        if source:
            params["source"] = source
        if search:
            params["search"] = search
        return self.request("GET", "/tasks", params=params, timeout=60)

    def settings(self) -> dict[str, Any]:
        return self.request("GET", "/api/settings", timeout=60)

    @staticmethod
    def _response_detail(response: httpx.Response) -> Any:
        try:
            return response.json()
        except Exception:
            return response.text


def task_summary(task: Task, markdown_preview_length: int = 1200) -> TaskSummary:
    markdown = task.result_md or ""
    content_list = task.content_list or []
    pages = task.pages or []
    task_id = task.id
    return TaskSummary(
        task_id=task_id,
        status=task.status,
        original_name=task.original_name,
        backend=task.backend,
        lang=task.lang,
        file_size=task.file_size,
        pages_count=len(pages),
        content_blocks_count=len(content_list),
        markdown_length=len(markdown),
        markdown_preview=markdown[:markdown_preview_length],
        has_large_content=len(markdown) > markdown_preview_length or len(content_list) > DEFAULT_BLOCK_LIMIT,
        error=task.error,
        created_at=task.created_at,
        completed_at=task.completed_at,
        next_commands=[
            f"ocrplane markdown {task_id} --json --offset 0 --max-length {DEFAULT_MARKDOWN_LENGTH}",
            f"ocrplane blocks {task_id} --json --offset 0 --limit {DEFAULT_BLOCK_LIMIT}",
        ],
    )
