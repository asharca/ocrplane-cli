from __future__ import annotations

import base64
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin

import httpx
from fastmcp import FastMCP

mcp = FastMCP("ocr-mcp")

DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_POLL_INTERVAL_SECONDS = 3
DEFAULT_MARKDOWN_LENGTH = 12000
DEFAULT_BLOCK_LIMIT = 50
MAX_BASE64_BYTES = 25 * 1024 * 1024

Backend = Literal[
    "pipeline",
    "vlm-auto-engine",
    "hybrid-auto-engine",
    "vlm-http-client",
    "hybrid-http-client",
]
ParseMethod = Literal["auto", "ocr", "txt"]


class OcrPlaneError(RuntimeError):
    pass


def _base_url() -> str:
    value = (
        os.getenv("OCRPLANE_BASE_URL")
        or os.getenv("MINERU_API_BASE_URL")
        or os.getenv("MINERU_BASE_URL")
        or ""
    ).strip()
    if not value:
        raise OcrPlaneError("OCRPLANE_BASE_URL is not configured.")
    return value.rstrip("/") + "/"


def _api_key() -> str:
    value = (os.getenv("OCRPLANE_API_KEY") or os.getenv("MINERU_API_KEY") or "").strip()
    if not value:
        raise OcrPlaneError("OCRPLANE_API_KEY is not configured.")
    return value


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_api_key()}"}


def _url(path: str) -> str:
    return urljoin(_base_url(), path.lstrip("/"))


def _request(method: str, path: str, **kwargs: Any) -> Any:
    timeout = kwargs.pop("timeout", 60)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.request(method, _url(path), headers=_headers(), **kwargs)
    if response.status_code >= 400:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise OcrPlaneError(f"OcrPlane request failed: {response.status_code} {detail}")
    if not response.content:
        return None
    return response.json()


def _download_file(file_url: str) -> tuple[str, str]:
    suffix = Path(file_url.split("?", 1)[0]).suffix or ".bin"
    with httpx.Client(timeout=300, follow_redirects=True) as client:
        response = client.get(file_url)
        response.raise_for_status()
        fd, path = tempfile.mkstemp(prefix="ocr-mcp-", suffix=suffix)
        with os.fdopen(fd, "wb") as fh:
            fh.write(response.content)
    return path, Path(path).name


def _decode_base64(content: str, filename: str) -> tuple[str, str]:
    raw = base64.b64decode(content)
    if len(raw) > MAX_BASE64_BYTES:
        raise OcrPlaneError("base64_content is too large; use file_url for large documents.")
    suffix = Path(filename).suffix or ".bin"
    fd, path = tempfile.mkstemp(prefix="ocr-mcp-", suffix=suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
    return path, filename


def _resolve_upload(
    file_path: str | None,
    file_url: str | None,
    base64_content: str | None,
    filename: str | None,
) -> tuple[str, str, bool]:
    if file_path:
        path = Path(file_path).expanduser()
        if not path.exists() or not path.is_file():
            raise OcrPlaneError(f"file_path does not exist or is not a file: {file_path}")
        return str(path), filename or path.name, False
    if file_url:
        path, generated_name = _download_file(file_url)
        return path, filename or generated_name, True
    if base64_content:
        return (*_decode_base64(base64_content, filename or "document.bin"), True)
    raise OcrPlaneError("Provide one of file_path, file_url, or base64_content.")


def _upload_parse(
    path: str,
    filename: str,
    backend: Backend,
    lang: str,
    parse_method: ParseMethod,
    formula_enable: bool,
    table_enable: bool,
    start_page_id: int | None,
    end_page_id: int | None,
) -> dict[str, Any]:
    data: list[tuple[str, str]] = [
        ("backend", backend),
        ("parse_method", parse_method),
        ("formula_enable", "true" if formula_enable else "false"),
        ("table_enable", "true" if table_enable else "false"),
    ]
    for lang_part in [part.strip() for part in lang.split(",") if part.strip()]:
        data.append(("lang_list", lang_part))
    if start_page_id is not None:
        data.append(("start_page_id", str(start_page_id)))
    if end_page_id is not None:
        data.append(("end_page_id", str(end_page_id)))

    with open(path, "rb") as fh:
        files = {"file": (filename, fh)}
        return _request("POST", "/api/parse", data=data, files=files, timeout=120)


def _task_summary(task: dict[str, Any], markdown_preview_length: int = 1200) -> dict[str, Any]:
    markdown = task.get("result_md") or ""
    content_list = task.get("content_list") or []
    pages = task.get("pages") or []
    return {
        "task_id": task.get("id"),
        "status": task.get("status"),
        "original_name": task.get("original_name"),
        "backend": task.get("backend"),
        "lang": task.get("lang"),
        "file_size": task.get("file_size"),
        "pages_count": len(pages) if isinstance(pages, list) else None,
        "content_blocks_count": len(content_list) if isinstance(content_list, list) else None,
        "markdown_length": len(markdown),
        "markdown_preview": markdown[:markdown_preview_length],
        "has_large_content": len(markdown) > markdown_preview_length
        or (isinstance(content_list, list) and len(content_list) > DEFAULT_BLOCK_LIMIT),
        "error": task.get("error"),
        "created_at": task.get("created_at"),
        "completed_at": task.get("completed_at"),
    }


def _poll_task(task_id: str, timeout_seconds: int, poll_interval_seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_task: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        task = get_task_status(task_id)
        last_task = task
        if task.get("status") in {"completed", "failed"}:
            return task
        time.sleep(max(1, poll_interval_seconds))
    raise OcrPlaneError(f"Timed out waiting for task {task_id}. Last status: {last_task}")


@mcp.tool
def parse_document(
    file_path: str | None = None,
    file_url: str | None = None,
    base64_content: str | None = None,
    filename: str | None = None,
    backend: Backend = "pipeline",
    lang: str = "ch",
    parse_method: ParseMethod = "auto",
    formula_enable: bool = True,
    table_enable: bool = True,
    start_page_id: int | None = None,
    end_page_id: int | None = None,
    wait: bool = True,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """Submit a document to OcrPlane OCR and optionally wait for completion.

    Provide exactly one document input: file_path, file_url, or base64_content.
    For large files, prefer file_url. The tool returns a compact summary; use
    get_markdown or get_content_blocks to page through large results.
    """
    upload_path, upload_name, cleanup = _resolve_upload(file_path, file_url, base64_content, filename)
    try:
        created = _upload_parse(
            upload_path,
            upload_name,
            backend,
            lang,
            parse_method,
            formula_enable,
            table_enable,
            start_page_id,
            end_page_id,
        )
    finally:
        if cleanup:
            try:
                os.remove(upload_path)
            except OSError:
                pass

    task_id = str(created.get("id"))
    if not wait or created.get("status") == "pending":
        if not wait:
            return {"task_id": task_id, "status": created.get("status"), "message": created.get("message")}
    task = _poll_task(task_id, timeout_seconds, poll_interval_seconds)
    return _task_summary(task)


@mcp.tool
def get_task_status(task_id: str) -> dict[str, Any]:
    """Return the current OcrPlane task record for a task id."""
    return _request("GET", f"/tasks/{task_id}", timeout=60)


@mcp.tool
def get_markdown(task_id: str, offset: int = 0, max_length: int = DEFAULT_MARKDOWN_LENGTH) -> dict[str, Any]:
    """Return a slice of a task's markdown result."""
    task = get_task_status(task_id)
    markdown = task.get("result_md") or ""
    start = max(0, offset)
    length = max(1, min(max_length, 60000))
    end = min(len(markdown), start + length)
    return {
        "task_id": task_id,
        "status": task.get("status"),
        "offset": start,
        "max_length": length,
        "next_offset": end if end < len(markdown) else None,
        "total_length": len(markdown),
        "markdown": markdown[start:end],
    }


@mcp.tool
def get_content_blocks(
    task_id: str,
    offset: int = 0,
    limit: int = DEFAULT_BLOCK_LIMIT,
    page_idx: int | None = None,
) -> dict[str, Any]:
    """Return paginated structured content blocks, optionally filtered by page_idx."""
    task = get_task_status(task_id)
    blocks = task.get("content_list") or []
    if page_idx is not None:
        blocks = [block for block in blocks if block.get("page_idx") == page_idx]
    start = max(0, offset)
    size = max(1, min(limit, 200))
    end = min(len(blocks), start + size)
    return {
        "task_id": task_id,
        "status": task.get("status"),
        "offset": start,
        "limit": size,
        "next_offset": end if end < len(blocks) else None,
        "total_blocks": len(blocks),
        "blocks": blocks[start:end],
    }


@mcp.tool
def get_full_result(
    task_id: str,
    max_markdown_length: int = DEFAULT_MARKDOWN_LENGTH,
    max_blocks: int = DEFAULT_BLOCK_LIMIT,
) -> dict[str, Any]:
    """Return task metadata plus capped markdown and content blocks for small documents."""
    task = get_task_status(task_id)
    markdown = task.get("result_md") or ""
    blocks = task.get("content_list") or []
    return {
        **_task_summary(task),
        "markdown": markdown[: max(1, min(max_markdown_length, 60000))],
        "content_blocks": blocks[: max(1, min(max_blocks, 200))],
        "pages": task.get("pages") or [],
        "markdown_truncated": len(markdown) > max_markdown_length,
        "blocks_truncated": len(blocks) > max_blocks,
    }


@mcp.tool
def reprocess_task(
    task_id: str,
    rotate: int | None = None,
    rotate_pages: list[int] | None = None,
    rotations: dict[str, int] | None = None,
    page_indices: list[int] | None = None,
    backend: str | None = None,
    lang: str | None = None,
    parse_method: str | None = None,
    formula_enable: bool | None = None,
    table_enable: bool | None = None,
) -> dict[str, Any]:
    """Reprocess an existing task, optionally rotating or re-OCRing selected pages."""
    payload: dict[str, Any] = {}
    for key, value in {
        "rotate": rotate,
        "rotate_pages": rotate_pages,
        "rotations": rotations,
        "page_indices": page_indices,
        "backend": backend,
        "lang": lang,
        "parse_method": parse_method,
        "formula_enable": formula_enable,
        "table_enable": table_enable,
    }.items():
        if value is not None:
            payload[key] = value
    return _request("POST", f"/tasks/{task_id}/reprocess", json=payload, timeout=60)


@mcp.tool
def list_tasks(page: int = 1, limit: int = 20, source: str | None = "api", search: str | None = None) -> dict[str, Any]:
    """List OcrPlane tasks for the configured API key user."""
    params: dict[str, Any] = {"page": page, "limit": min(max(1, limit), 100)}
    if source:
        params["source"] = source
    if search:
        params["search"] = search
    return _request("GET", "/tasks", params=params, timeout=60)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

