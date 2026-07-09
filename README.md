# ocr-mcp

MCP wrapper for OcrPlane document OCR APIs. It exposes high-level tools for
document parsing, async polling, paginated markdown access, content block
pagination, reprocessing, and task listing.

## ToolPlane Deployment

Build/publish this repo's Docker image, then deploy it in ToolPlane as a custom
Docker MCP server:

```txt
Source: Docker
Image: ghcr.io/asharca/ocr-mcp:latest
Name: OcrPlane OCR
```

In the deployment's **Variables** tab, configure:

```txt
OCRPLANE_BASE_URL=https://your-ocrplane.example.com
OCRPLANE_API_KEY=mk_xxxxxxxxxxxxxxxxxxxx
```

Compatibility aliases are also supported:

```txt
MINERU_API_BASE_URL
MINERU_BASE_URL
MINERU_API_KEY
```

Prefer the `OCRPLANE_*` names for new deployments.

## Tools

- `parse_document`: submit a document and optionally wait for completion.
- `get_task_status`: return the raw task record.
- `get_markdown`: read markdown with `offset` and `max_length`.
- `get_content_blocks`: read structured blocks with `offset`, `limit`, and optional `page_idx`.
- `get_full_result`: capped full result for small documents.
- `reprocess_task`: rotate/reprocess existing tasks.
- `list_tasks`: list task history for the configured API key user.

`parse_document` accepts one of:

- `file_path`: a file path inside the MCP container.
- `file_url`: recommended for large files.
- `base64_content`: small-file fallback, capped at 25 MB decoded.

## Local Run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .

export OCRPLANE_BASE_URL=http://localhost:3001
export OCRPLANE_API_KEY=mk_xxxxxxxxxxxxxxxxxxxx
ocr-mcp
```

The server uses FastMCP's default STDIO transport.

