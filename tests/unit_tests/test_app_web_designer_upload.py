# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``POST /file-api/upload`` from the Design canvas on the production static server."""

from __future__ import annotations

import json
import threading
import uuid
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import pytest

from jiuwenswarm.channels.web import designer_upload_http
from jiuwenswarm.channels.web.app_web import _SpaStaticHandler
from jiuwenswarm.channels.web.designer_upload_http import handle_designer_upload_http

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _multipart(filename: str, content: bytes, mime: str = "image/png", field: str = "file") -> tuple[str, bytes]:
    boundary = f"----designer{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return f"multipart/form-data; boundary={boundary}", body


@pytest.fixture
def server(tmp_path: Path):
    class Handler(_SpaStaticHandler):
        project_root = tmp_path
        workspace_root = tmp_path / "agent"
        agent_teams_root = tmp_path / "agent-teams"
        logs_root = tmp_path / "logs"
        auto_harness_root = tmp_path / "auto-harness"
        projectless_tasks_root = tmp_path / "tasks"
        api_target = ""
        ws_target = ""

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever)
    thread.start()
    try:
        yield httpd.server_port, tmp_path
    finally:
        httpd.shutdown()
        thread.join()
        httpd.server_close()


def _request(port: int, method: str, path: str, body: bytes = b"", headers: dict | None = None):
    conn = HTTPConnection("127.0.0.1", port, timeout=30)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response, data


def test_designer_upload_is_stored_and_previewable(server) -> None:
    port, root = server
    content_type, body = _multipart("小狐狸 ref.png", _PNG)
    response, data = _request(port, "POST", "/file-api/upload", body, {"Content-Type": content_type})
    assert response.status == 200, data
    payload = json.loads(data)
    assert payload["ok"] is True and payload["errors"] == []
    item = payload["files"][0]
    stored = Path(item["path"])
    assert stored.parent == root / "agent" / "uploads" / "designer"
    assert stored.read_bytes() == _PNG
    assert item["filename"] == "小狐狸 ref.png"
    assert item["mime_type"] == "image/png"
    assert item["size"] == len(_PNG)

    # The canvas previews the upload through /file-api/raw-file.
    preview, raw = _request(port, "GET", f"/file-api/raw-file?path={quote(str(stored))}")
    assert preview.status == 200
    assert raw == _PNG


def test_oversized_upload_gets_413_not_a_reset_connection(server, monkeypatch) -> None:
    port, root = server
    monkeypatch.setattr(designer_upload_http, "MAX_DESIGNER_UPLOAD_BYTES", 1024)
    content_type, body = _multipart("big.mp4", b"\x00" * 200_000, "video/mp4")
    response, data = _request(port, "POST", "/file-api/upload", body, {"Content-Type": content_type})
    assert response.status == 413
    assert json.loads(data)["errors"] == [{"error": "file_too_large"}]
    assert not (root / "agent" / "uploads" / "designer").exists()


def test_token_uploads_still_require_a_token(server) -> None:
    port, _ = server
    response, data = _request(
        port, "POST", "/file-api/upload", b"raw-bytes", {"Content-Type": "application/octet-stream"},
    )
    assert response.status == 400
    assert json.loads(data) == {"error": "missing_token"}


def test_filename_is_sanitised(tmp_path: Path) -> None:
    content_type, body = _multipart("../../evil<>:name.png", _PNG)
    status, payload = handle_designer_upload_http(content_type=content_type, body=body, upload_dir=tmp_path / "up")
    assert status == 200
    stored = Path(payload["files"][0]["path"])
    assert stored.parent == tmp_path / "up"
    assert stored.name.endswith("_evil___name.png")


@pytest.mark.parametrize(
    ("content_type", "body", "status", "code"),
    [
        ("multipart/form-data; boundary=x", b"garbage", 400, "invalid_multipart"),
        ("text/plain", b"hello", 400, "invalid_multipart"),
    ],
)
def test_bad_requests(tmp_path: Path, content_type: str, body: bytes, status: int, code: str) -> None:
    got_status, payload = handle_designer_upload_http(content_type=content_type, body=body, upload_dir=tmp_path)
    assert (got_status, payload["errors"][0]["error"]) == (status, code)


def test_wrong_field_name_is_missing_file(tmp_path: Path) -> None:
    content_type, body = _multipart("a.png", _PNG, field="attachment")
    status, payload = handle_designer_upload_http(content_type=content_type, body=body, upload_dir=tmp_path)
    assert (status, payload["error"]) == (400, "missing_file")
