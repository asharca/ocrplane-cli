from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from .client import DEFAULT_BLOCK_LIMIT, DEFAULT_MARKDOWN_LENGTH, OcrPlaneClient, task_summary
from .models import Backend, DryRunPlan, OcrPlaneError, ParseMethod, ParseOptions, Task

app = typer.Typer(
    name="ocrplane",
    help="Agent-friendly CLI for OcrPlane/MineRU OCR APIs.",
    no_args_is_help=True,
)
console = Console()

JsonFlag = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")]
DryRunFlag = Annotated[bool, typer.Option("--dry-run", help="Validate input and show the API request without sending it.")]


def _client(allow_missing_auth: bool = False) -> OcrPlaneClient:
    return OcrPlaneClient(allow_missing_auth=allow_missing_auth)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _print(value: Any, json_output: bool = False) -> None:
    if json_output:
        sys.stdout.write(json.dumps(_jsonable(value), ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
        return
    if isinstance(value, DryRunPlan):
        console.print(Panel.fit(json.dumps(value.model_dump(mode="json"), ensure_ascii=False, indent=2), title="Dry run"))
        return
    console.print(value)


def _exit_with_error(error: Exception, json_output: bool = False) -> None:
    payload = {"error": str(error)}
    if json_output:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
    else:
        console.print(f"[bold red]Error:[/bold red] {error}")
    raise typer.Exit(1)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(value), ensure_ascii=False, indent=2), encoding="utf-8")


def _render_summary(summary: Any) -> None:
    data = _jsonable(summary)
    table = Table(title="OCR Task Summary", show_header=True, header_style="bold")
    table.add_column("Field")
    table.add_column("Value")
    for key in [
        "task_id",
        "status",
        "original_name",
        "backend",
        "lang",
        "file_size",
        "pages_count",
        "content_blocks_count",
        "markdown_length",
        "has_large_content",
        "error",
    ]:
        table.add_row(key, str(data.get(key)))
    console.print(table)
    preview = data.get("markdown_preview")
    if preview:
        console.print(Panel(preview, title="Markdown preview"))
    next_commands = data.get("next_commands") or []
    if next_commands:
        console.print(Panel("\n".join(next_commands), title="Next commands"))


def _render_task(task: Task) -> None:
    table = Table(title=f"OCR Task {task.id}", show_header=True, header_style="bold")
    table.add_column("Field")
    table.add_column("Value")
    for key, value in task.model_dump(mode="json", exclude={"result_md", "content_list", "pages"}).items():
        table.add_row(key, str(value))
    console.print(table)


@app.command()
def parse(
    file: Annotated[Path, typer.Argument(help="Document path. Use /workspace/... inside sandboxes.")],
    backend: Annotated[Backend, typer.Option(help="MineRU backend.")] = "pipeline",
    lang: Annotated[str, typer.Option(help='Language code, for example "ch" or "ch,en".')] = "ch",
    parse_method: Annotated[ParseMethod, typer.Option("--parse-method", help="Parse method.")] = "auto",
    formula_enable: Annotated[bool, typer.Option("--formula/--no-formula", help="Enable formula recognition.")] = True,
    table_enable: Annotated[bool, typer.Option("--table/--no-table", help="Enable table recognition.")] = True,
    start_page_id: Annotated[int | None, typer.Option("--start-page", help="Zero-based first page to parse.")] = None,
    end_page_id: Annotated[int | None, typer.Option("--end-page", help="Zero-based last page to parse.")] = None,
    filename: Annotated[str | None, typer.Option(help="Override uploaded filename.")] = None,
    wait: Annotated[bool, typer.Option("--wait/--no-wait", help="Poll until task completes.")] = True,
    timeout_seconds: Annotated[int, typer.Option("--timeout", help="Polling timeout in seconds.")] = 900,
    poll_interval_seconds: Annotated[int, typer.Option("--poll-interval", help="Polling interval in seconds.")] = 3,
    save_dir: Annotated[Path | None, typer.Option("--save-dir", help="Write summary/result files into this directory.")] = None,
    json_output: JsonFlag = False,
    dry_run: DryRunFlag = False,
) -> None:
    """Submit a document for OCR."""
    try:
        options = ParseOptions(
            file_path=file,
            filename=filename,
            backend=backend,
            lang=lang,
            parse_method=parse_method,
            formula_enable=formula_enable,
            table_enable=table_enable,
            start_page_id=start_page_id,
            end_page_id=end_page_id,
        )
        client = _client(allow_missing_auth=dry_run)
        if dry_run:
            plan = client.dry_run("parse", "POST", "/api/parse", options.form_data())
            plan.files.append(str(options.file_path))
            _print(plan, json_output)
            return
        created = client.parse(options)
        if not wait:
            _print(created, json_output)
            return

        if json_output:
            task = client.poll_task(created.id, timeout_seconds, poll_interval_seconds)
        else:
            task_holder: dict[str, Task] = {}
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                TimeElapsedColumn(),
                transient=True,
                console=console,
            ) as progress:
                progress_task = progress.add_task(f"Waiting for OCR task {created.id}", total=None)

                def on_tick(task: Task) -> None:
                    task_holder["task"] = task
                    progress.update(progress_task, description=f"Task {task.id}: {task.status} {task.progress or ''}")

                task = client.poll_task(created.id, timeout_seconds, poll_interval_seconds, on_tick=on_tick)

        summary = task_summary(task)
        if save_dir is not None:
            _write_json(save_dir / "summary.json", summary)
            if task.result_md:
                (save_dir / "result.md").write_text(task.result_md, encoding="utf-8")
            if task.content_list is not None:
                _write_json(save_dir / "content_blocks.json", task.content_list)
            if task.pages is not None:
                _write_json(save_dir / "pages.json", task.pages)
        if json_output:
            _print(summary, True)
        else:
            _render_summary(summary)
    except (OcrPlaneError, ValidationError, ValueError) as error:
        _exit_with_error(error, json_output)


@app.command()
def status(
    task_id: Annotated[str, typer.Argument(help="OCR task id.")],
    json_output: JsonFlag = False,
    dry_run: DryRunFlag = False,
) -> None:
    """Get a task's current status and metadata."""
    try:
        client = _client(allow_missing_auth=dry_run)
        if dry_run:
            _print(client.dry_run("status", "GET", f"/tasks/{task_id}"), json_output)
            return
        task = client.get_task(task_id)
        if json_output:
            _print(task, True)
        else:
            _render_task(task)
    except (OcrPlaneError, ValidationError) as error:
        _exit_with_error(error, json_output)


@app.command()
def markdown(
    task_id: Annotated[str, typer.Argument(help="OCR task id.")],
    offset: Annotated[int, typer.Option(help="Character offset.")] = 0,
    max_length: Annotated[int, typer.Option("--max-length", help="Maximum characters to return.")] = DEFAULT_MARKDOWN_LENGTH,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write markdown slice to a file.")] = None,
    json_output: JsonFlag = False,
    dry_run: DryRunFlag = False,
) -> None:
    """Read a bounded markdown slice from a completed task."""
    try:
        client = _client(allow_missing_auth=dry_run)
        if dry_run:
            _print(
                client.dry_run("markdown", "GET", f"/tasks/{task_id}", {"offset": offset, "max_length": max_length}),
                json_output,
            )
            return
        result = client.markdown(task_id, offset, max_length)
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(result.markdown, encoding="utf-8")
        if json_output:
            _print(result, True)
        else:
            console.print(result.markdown)
            if result.next_offset is not None:
                console.print(f"\n[dim]Next: ocrplane markdown {task_id} --offset {result.next_offset}[/dim]")
    except (OcrPlaneError, ValidationError) as error:
        _exit_with_error(error, json_output)


@app.command()
def blocks(
    task_id: Annotated[str, typer.Argument(help="OCR task id.")],
    offset: Annotated[int, typer.Option(help="Block offset.")] = 0,
    limit: Annotated[int, typer.Option(help="Maximum blocks to return.")] = DEFAULT_BLOCK_LIMIT,
    page_idx: Annotated[int | None, typer.Option("--page-idx", help="Only return blocks from this page index.")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write JSON blocks page to a file.")] = None,
    json_output: JsonFlag = False,
    dry_run: DryRunFlag = False,
) -> None:
    """Read paginated structured content blocks."""
    try:
        client = _client(allow_missing_auth=dry_run)
        if dry_run:
            _print(
                client.dry_run(
                    "blocks",
                    "GET",
                    f"/tasks/{task_id}",
                    {"offset": offset, "limit": limit, "page_idx": page_idx},
                ),
                json_output,
            )
            return
        result = client.content_blocks(task_id, offset, limit, page_idx)
        if output is not None:
            _write_json(output, result)
        _print(result, json_output)
    except (OcrPlaneError, ValidationError) as error:
        _exit_with_error(error, json_output)


@app.command()
def result(
    task_id: Annotated[str, typer.Argument(help="OCR task id.")],
    max_markdown_length: Annotated[int, typer.Option("--max-markdown-length", help="Maximum markdown characters.")] = DEFAULT_MARKDOWN_LENGTH,
    max_blocks: Annotated[int, typer.Option("--max-blocks", help="Maximum content blocks.")] = DEFAULT_BLOCK_LIMIT,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write capped result JSON to a file.")] = None,
    json_output: JsonFlag = False,
    dry_run: DryRunFlag = False,
) -> None:
    """Read a capped full result for small documents."""
    try:
        client = _client(allow_missing_auth=dry_run)
        if dry_run:
            _print(
                client.dry_run(
                    "result",
                    "GET",
                    f"/tasks/{task_id}",
                    {"max_markdown_length": max_markdown_length, "max_blocks": max_blocks},
                ),
                json_output,
            )
            return
        data = client.full_result(task_id, max_markdown_length, max_blocks)
        if output is not None:
            _write_json(output, data)
        _print(data, json_output)
    except (OcrPlaneError, ValidationError) as error:
        _exit_with_error(error, json_output)


@app.command("list")
def list_tasks(
    page: Annotated[int, typer.Option(help="Page number.")] = 1,
    limit: Annotated[int, typer.Option(help="Page size.")] = 20,
    source: Annotated[str | None, typer.Option(help="Filter by source: api, web, or empty for all.")] = "api",
    search: Annotated[str | None, typer.Option(help="Search filename.")] = None,
    json_output: JsonFlag = False,
    dry_run: DryRunFlag = False,
) -> None:
    """List OCR tasks visible to the configured API key."""
    try:
        client = _client(allow_missing_auth=dry_run)
        params = {"page": page, "limit": limit, "source": source, "search": search}
        if dry_run:
            _print(client.dry_run("list", "GET", "/tasks", params), json_output)
            return
        data = client.list_tasks(page, limit, source, search)
        _print(data, json_output)
    except OcrPlaneError as error:
        _exit_with_error(error, json_output)


@app.command()
def reprocess(
    task_id: Annotated[str, typer.Argument(help="OCR task id.")],
    rotate: Annotated[int | None, typer.Option(help="Rotate all pages by degrees.")] = None,
    rotate_pages: Annotated[list[int] | None, typer.Option("--rotate-page", help="Page index to rotate; repeatable.")] = None,
    page_indices: Annotated[list[int] | None, typer.Option("--page", help="Page index to reprocess; repeatable.")] = None,
    backend: Annotated[str | None, typer.Option(help="Override backend.")] = None,
    lang: Annotated[str | None, typer.Option(help="Override language.")] = None,
    parse_method: Annotated[str | None, typer.Option("--parse-method", help="Override parse method.")] = None,
    formula_enable: Annotated[bool | None, typer.Option("--formula/--no-formula", help="Override formula recognition.")] = None,
    table_enable: Annotated[bool | None, typer.Option("--table/--no-table", help="Override table recognition.")] = None,
    json_output: JsonFlag = False,
    dry_run: DryRunFlag = False,
) -> None:
    """Start reprocessing for an existing task."""
    try:
        payload: dict[str, Any] = {}
        for key, value in {
            "rotate": rotate,
            "rotate_pages": rotate_pages,
            "page_indices": page_indices,
            "backend": backend,
            "lang": lang,
            "parse_method": parse_method,
            "formula_enable": formula_enable,
            "table_enable": table_enable,
        }.items():
            if value is not None:
                payload[key] = value
        client = _client(allow_missing_auth=dry_run)
        if dry_run:
            _print(client.dry_run("reprocess", "POST", f"/tasks/{task_id}/reprocess", payload), json_output)
            return
        created = client.reprocess(task_id, payload)
        _print(created, json_output)
    except (OcrPlaneError, ValidationError) as error:
        _exit_with_error(error, json_output)


@app.command()
def settings(json_output: JsonFlag = False, dry_run: DryRunFlag = False) -> None:
    """Read OCR defaults for the configured user."""
    try:
        client = _client(allow_missing_auth=dry_run)
        if dry_run:
            _print(client.dry_run("settings", "GET", "/api/settings"), json_output)
            return
        _print(client.settings(), json_output)
    except OcrPlaneError as error:
        _exit_with_error(error, json_output)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
