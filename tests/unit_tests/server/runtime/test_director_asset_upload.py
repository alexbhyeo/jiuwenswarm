# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the director 素材 multipart upload endpoint.

Covers ``handle_director_asset_upload_http`` (``POST /file-api/director/upload``):
extension-based type inference for image/video/audio, the explicit
``asset_type`` selection (including the ``character`` special case) and the
mismatch/unsupported-extension rejections.

Every filesystem path is redirected into ``tmp_path`` by patching
``get_agent_root_dir`` in the ``director_store`` module - all of its path
helpers derive from that one function, so a single patch isolates both
``director_state.json`` and the per-project ``assets/`` directory and the test
never touches the real ``~/.jiuwenswarm``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenswarm.server.runtime.director import director_store as ds
from jiuwenswarm.server.runtime.director.director_multipart_http import (
    handle_director_asset_upload_http,
)

_BOUNDARY = "----DirectorAssetUploadBoundary"


def _multipart_body(*, filename: str, content: bytes, extra_fields: dict[str, str] | None = None) -> bytes:
    """Build a minimal one-file multipart/form-data body."""
    body = b""
    for name, value in (extra_fields or {}).items():
        body += (
            f"--{_BOUNDARY}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode()
    body += (
        f"--{_BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    body += content + b"\r\n"
    body += f"--{_BOUNDARY}--\r\n".encode()
    return body


def _upload(*, filename: str, asset_type: str | None = None) -> tuple[int, dict]:
    fields = {"project_id": _project_id}
    if asset_type is not None:
        fields["asset_type"] = asset_type
    body = _multipart_body(filename=filename, content=b"payload-bytes", extra_fields=fields)
    return handle_director_asset_upload_http(
        content_type=f'multipart/form-data; boundary="{_BOUNDARY}"',
        body=body,
    )


_project_id = ""


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the director store at tmp_path and seed one project."""
    global _project_id
    monkeypatch.setattr(ds, "get_agent_root_dir", lambda: tmp_path)
    _project_id = ds.DirectorStore().create_project("demo").project_id


def test_audio_extension_infers_audio_type() -> None:
    status, payload = _upload(filename="narration.mp3")

    assert status == 200
    assert payload["asset_counts"]["audio"] == 1
    asset = next(a for a in payload["project"]["assets"] if a["asset_id"] == payload["asset_id"])
    assert asset["type"] == "audio"
    assert asset["status"] == "ready"
    assert asset["name"] == "narration"
    assert asset["params"]["source"] == "upload"
    assert Path(asset["file_path"]).suffix == ".mp3"
    assert Path(asset["file_path"]).exists()


def test_explicit_audio_asset_type_accepted() -> None:
    status, payload = _upload(filename="voice.wav", asset_type="audio")

    assert status == 200
    asset = next(a for a in payload["project"]["assets"] if a["asset_id"] == payload["asset_id"])
    assert asset["type"] == "audio"


def test_audio_asset_type_rejects_image_file() -> None:
    status, payload = _upload(filename="poster.png", asset_type="audio")

    assert status == 400
    assert payload["code"] == "INVALID_PARAMS"
    assert "不匹配" in payload["message"]


def test_unsupported_extension_rejected() -> None:
    status, payload = _upload(filename="archive.zip", asset_type="audio")

    assert status == 400
    assert payload["code"] == "INVALID_PARAMS"
    assert "不支持的文件类型" in payload["message"]


def test_image_and_video_inference_unchanged() -> None:
    assert _upload(filename="photo.png")[1]["project"]["assets"][-1]["type"] == "image"
    assert _upload(filename="clip.mp4")[1]["project"]["assets"][-1]["type"] == "video"


def test_character_requires_image_file() -> None:
    status, payload = _upload(filename="hero.mp3", asset_type="character")

    assert status == 400
    assert payload["code"] == "INVALID_PARAMS"
    assert "角色素材只能上传图片文件" in payload["message"]


def test_missing_project_id_rejected() -> None:
    body = _multipart_body(filename="track.mp3", content=b"payload-bytes")
    status, payload = handle_director_asset_upload_http(
        content_type=f'multipart/form-data; boundary="{_BOUNDARY}"',
        body=body,
    )

    assert status == 400
    assert payload["code"] == "INVALID_PARAMS"
    assert "缺少 project_id" in payload["message"]
