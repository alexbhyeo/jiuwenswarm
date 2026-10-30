# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the Lab's 图音生视频 (``image_audio2video``) mode.

The mode is one reference-to-video call: the connected reference image and the
connected reference audio both travel as multimodal references, and the model
lip-syncs the generated clip to that audio. These tests pin the parts that only
exist because of that wiring:

* the reference image is taken from ``reference_asset_ids`` (the image1 port),
  not from the first/last-frame slots - sending both ``frame_images`` and
  ``input_references`` makes the provider drop the references,
* the audio must be a *ready audio* asset, and the image a *ready image*,
* both paths are recorded in the persisted asset's ``params`` so the frontend
  can rebuild the dependency edges when it restores the canvas,
* the persisted asset is a ``video`` asset (mode says how it was made, type
  says what came out).

``generate_video`` itself is stubbed here; its own reference handling is
covered by ``tests/unit_tests/agents/test_video_gen_tools.py``.

Every filesystem path is redirected into ``tmp_path`` by patching
``get_agent_root_dir`` in the ``director_store`` module - all of its path helpers
derive from that one function, so a single patch isolates both
``director_state.json`` and the per-project ``assets/`` directory.
"""

from __future__ import annotations

import os

from pathlib import Path
from typing import Any

import pytest

from jiuwenswarm.server.runtime.director import director_manager as dm
from jiuwenswarm.server.runtime.director import director_store as ds
from jiuwenswarm.server.runtime.director.director_manager import (
    DirectorManager,
    DirectorRpcError,
)

_SAVED = "Video generated successfully!\nSaved to: {path}"


class _StubVideoTool:
    """Stands in for ``video_gen_tools.generate_video`` (a LocalFunction)."""

    def __init__(self, result: str) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def _func(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.result


@pytest.fixture
def isolated_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the director store (and therefore the whole feature) at tmp_path."""
    monkeypatch.setattr(ds, "get_agent_root_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def manager(isolated_root: Path) -> DirectorManager:
    # Constructed after the path patch is in place.
    return DirectorManager()


def _configure(monkeypatch: pytest.MonkeyPatch, *, video: bool = True) -> None:
    monkeypatch.setattr(dm, "video_gen_enabled", lambda: video)
    monkeypatch.setattr(dm, "video_gen_configured", lambda: video)
    # 云存储默认按"未连接"处理，避免测试结果取决于跑测试这台机器上到底
    # 连没连 azure-blob-storage（开发机上真连了的话，不 patch 这个就会在
    # 跑测试时真的把素材传到真实 Azure 存储账户——这正是这组测试最初踩到
    # 的坑）。要测"连了云存储"的分支，用 _configure_cloud_storage。
    monkeypatch.setattr(dm, "get_mcp_server_config", lambda name: None)


def _stub_video_tool(monkeypatch: pytest.MonkeyPatch, result: str) -> _StubVideoTool:
    """Install a stub returning ``result`` verbatim - the manager classifies the
    outcome by parsing that string (see _parse_generation_result), so tests pass
    the exact tool-shaped text they want parsed."""
    stub = _StubVideoTool(result)
    monkeypatch.setattr(dm, "generate_video", stub)
    return stub


class _StubCloudUpload:
    """Stands in for ``call_mcp_tool`` when a cloud-storage connector is
    "connected" - records the (entry, tool_name, arguments) it was called
    with and either returns ``url`` or raises ``error``."""

    def __init__(self, *, url: str | None = None, error: Exception | None = None) -> None:
        self.url = url
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self, entry: dict[str, Any], tool_name: str, arguments: dict[str, Any], **kwargs: Any
    ) -> Any:
        self.calls.append({"entry": entry, "tool_name": tool_name, "arguments": arguments})
        if self.error is not None:
            raise self.error
        return self.url


def _configure_cloud_storage(
    monkeypatch: pytest.MonkeyPatch, *, url: str | None = None, error: Exception | None = None
) -> _StubCloudUpload:
    """Mark the azure-blob-storage connector as "connected" and stub its upload
    call. Must run after ``_configure`` (which defaults it to not-connected)."""
    # 只把 azure-blob-storage 标成"已连接"：候选表按
    # dm.CLOUD_STORAGE_MCP_CANDIDATES 顺序取第一个，让 tencent-cos 保持未连接
    # 才能继续走 azure 分支（它的目标 key 参数名是 blob_name，不是 object_key）。
    monkeypatch.setattr(
        dm, "get_mcp_server_config",
        lambda name: {"name": name, "transport": "stdio"} if name == "azure-blob-storage" else None,
    )
    stub = _StubCloudUpload(url=url, error=error)
    monkeypatch.setattr(dm, "call_mcp_tool", stub)
    return stub


async def _create_project(manager: DirectorManager, name: str = "图音生视频项目") -> str:
    created = await manager.handle_director_projects_create({"name": name})
    return created["project"]["project_id"]


def _add_asset(
    manager: DirectorManager,
    project_id: str,
    *,
    asset_id: str,
    asset_type: str,
    status: str = "ready",
    suffix: str,
) -> str:
    path = ds.get_project_assets_dir(project_id) / f"{asset_id}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"asset-bytes")
    manager._store.append_asset(
        project_id,
        ds.DirectorAsset(
            asset_id=asset_id,
            type=asset_type,
            status=status,
            prompt="ref",
            file_path=str(path),
        ),
    )
    return str(path)


def _asset_of(result: dict[str, Any]) -> dict[str, Any]:
    return next(a for a in result["project"]["assets"] if a["asset_id"] == result["asset_id"])


async def _generate(manager: DirectorManager, project_id: str, **params: Any) -> dict[str, Any]:
    return await manager.handle_director_generate(
        {
            "project_id": project_id,
            "mode": "image_audio2video",
            "reference_asset_ids": ["asset-image-1"],
            "input_audio_asset_id": "asset-audio-1",
            **params,
        }
    )


def test_image_audio2video_is_a_supported_mode() -> None:
    assert "image_audio2video" in dm._SUPPORTED_MODES


@pytest.mark.asyncio
async def test_generate_passes_reference_image_and_audio_to_the_tool(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    image_path = _add_asset(
        manager, project_id, asset_id="asset-image-1", asset_type="image", suffix=".png"
    )
    audio_path = _add_asset(
        manager, project_id, asset_id="asset-audio-1", asset_type="audio", suffix=".mp3"
    )
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path=str(tmp_path / "out.mp4")))

    result = await _generate(
        manager,
        project_id,
        prompt="@Image1 is a cute girl standing in a park. She lip-syncs to @Audio1.",
        duration_seconds=5,
    )

    assert len(stub.calls) == 1
    kwargs = stub.calls[0]["kwargs"]
    assert kwargs["reference_image_path"] == image_path
    assert kwargs["reference_audio_path"] == audio_path
    assert kwargs["duration_seconds"] == 5
    # The reference image must NOT also be sent as a first frame.
    assert "first_frame_path" not in kwargs
    # The clip comes back with an audio track unless the card says otherwise.
    assert kwargs["generate_audio"] is True

    asset = _asset_of(result)
    assert asset["type"] == "video"
    assert asset["status"] == "ready"
    assert asset["params"]["generate_audio"] is True
    assert asset["params"]["reference_image_path"] == image_path
    assert asset["params"]["input_audio_path"] == audio_path
    assert asset["params"]["input_audio_asset_id"] == "asset-audio-1"


@pytest.mark.asyncio
async def test_generate_falls_back_to_a_default_prompt(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    """两条参考连上就是一次完整请求——没写文案也不该被"提示词不能为空"拦住。"""
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id, asset_id="asset-image-1", asset_type="image", suffix=".png")
    _add_asset(manager, project_id, asset_id="asset-audio-1", asset_type="audio", suffix=".mp3")
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path=str(tmp_path / "out.mp4")))

    result = await _generate(manager, project_id)

    assert stub.calls[0]["kwargs"]["prompt"].startswith("Generate a video that lip-syncs")
    assert _asset_of(result)["prompt"].startswith("Generate a video that lip-syncs")


@pytest.mark.asyncio
async def test_generate_requires_a_ready_reference_image(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id, asset_id="asset-audio-1", asset_type="audio", suffix=".mp3")
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path=str(tmp_path / "out.mp4")))

    with pytest.raises(DirectorRpcError) as exc:
        await _generate(manager, project_id, prompt="p")

    assert exc.value.code == "INVALID_PARAMS"
    assert "参考图片" in exc.value.message
    assert stub.calls == []


@pytest.mark.asyncio
async def test_generate_rejects_a_non_image_reference(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    """参考图端口只认图片素材——拿一段视频当参考图必须明确报错。"""
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id, asset_id="asset-image-1", asset_type="video", suffix=".mp4")
    _add_asset(manager, project_id, asset_id="asset-audio-1", asset_type="audio", suffix=".mp3")
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path=str(tmp_path / "out.mp4")))

    with pytest.raises(DirectorRpcError) as exc:
        await _generate(manager, project_id, prompt="p")

    assert exc.value.code == "INVALID_PARAMS"
    assert "参考图片" in exc.value.message
    assert stub.calls == []


@pytest.mark.asyncio
async def test_generate_requires_a_ready_audio_asset(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id, asset_id="asset-image-1", asset_type="image", suffix=".png")
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path=str(tmp_path / "out.mp4")))

    with pytest.raises(DirectorRpcError) as exc:
        await _generate(manager, project_id, prompt="p")

    assert exc.value.code == "INVALID_PARAMS"
    assert "音频素材" in exc.value.message
    assert stub.calls == []


@pytest.mark.asyncio
async def test_generate_rejects_a_pending_audio_asset(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id, asset_id="asset-image-1", asset_type="image", suffix=".png")
    _add_asset(
        manager, project_id, asset_id="asset-audio-1", asset_type="audio", status="pending", suffix=".mp3"
    )
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path=str(tmp_path / "out.mp4")))

    with pytest.raises(DirectorRpcError) as exc:
        await _generate(manager, project_id, prompt="p")

    assert "音频素材" in exc.value.message
    assert stub.calls == []


@pytest.mark.asyncio
async def test_generate_requires_video_generation_to_be_configured(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    _configure(monkeypatch, video=False)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id, asset_id="asset-image-1", asset_type="image", suffix=".png")
    _add_asset(manager, project_id, asset_id="asset-audio-1", asset_type="audio", suffix=".mp3")
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path=str(tmp_path / "out.mp4")))

    with pytest.raises(DirectorRpcError) as exc:
        await _generate(manager, project_id, prompt="p")

    assert exc.value.code == "NOT_CONFIGURED"
    assert "视频生成未配置" in exc.value.message
    assert stub.calls == []


@pytest.mark.asyncio
async def test_generate_surfaces_a_generation_failure(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id, asset_id="asset-image-1", asset_type="image", suffix=".png")
    _add_asset(manager, project_id, asset_id="asset-audio-1", asset_type="audio", suffix=".mp3")
    _stub_video_tool(monkeypatch, "[ERROR]: video generation submit failed: 400 bad request")

    with pytest.raises(DirectorRpcError) as exc:
        await _generate(manager, project_id, prompt="p")

    assert exc.value.code == "GENERATION_FAILED"
    assert "400 bad request" in exc.value.message


@pytest.mark.asyncio
async def test_generate_keeps_a_pending_job_pollable(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    """Seedance 跑不完时返回 job_id——资产要落成 pending + job_id，让前端继续
    轮询 check_status，而不是当成失败。"""
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id, asset_id="asset-image-1", asset_type="image", suffix=".png")
    _add_asset(manager, project_id, asset_id="asset-audio-1", asset_type="audio", suffix=".mp3")
    _stub_video_tool(
        monkeypatch,
        "Video job job-xyz submitted and still running after 120s - call check_video_status.",
    )
    result = await _generate(manager, project_id, prompt="p")

    asset = _asset_of(result)
    assert asset["status"] == "pending"
    assert asset["job_id"] == "job-xyz"
    assert asset["type"] == "video"


@pytest.mark.asyncio
async def test_generate_can_produce_a_silent_clip(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    """卡片上的"无声音"选项：参考音频仍然驱动口型，但成片不带音轨。"""
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id, asset_id="asset-image-1", asset_type="image", suffix=".png")
    _add_asset(manager, project_id, asset_id="asset-audio-1", asset_type="audio", suffix=".mp3")
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path=str(tmp_path / "out.mp4")))

    result = await _generate(manager, project_id, prompt="p", generate_audio=False)

    assert stub.calls[0]["kwargs"]["generate_audio"] is False
    # The reference audio is still passed - only the output track is suppressed.
    assert stub.calls[0]["kwargs"]["reference_audio_path"]
    asset = _asset_of(result)
    assert asset["params"]["generate_audio"] is False
    assert asset["type"] == "video"


@pytest.mark.asyncio
async def test_generate_accepts_an_audio_url_instead_of_an_asset(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    """参考音频可以是一条公网直链（对象存储里的 wav），不必先上传成素材。"""
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id, asset_id="asset-image-1", asset_type="image", suffix=".png")
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path=str(tmp_path / "out.mp4")))
    url = "https://examdatalake.blob.core.windows.net/examdatalake/audio/song-short.wav"

    result = await manager.handle_director_generate(
        {
            "project_id": project_id,
            "mode": "image_audio2video",
            "reference_asset_ids": ["asset-image-1"],
            "input_audio_url": url,
            "prompt": "sing this",
        }
    )

    kwargs = stub.calls[0]["kwargs"]
    assert kwargs["reference_audio_path"] == url
    asset = _asset_of(result)
    assert asset["type"] == "video"
    assert asset["params"]["input_audio_path"] == url
    # 没有走素材那条路，就不该记一个素材 id 出来。
    assert asset["params"]["input_audio_asset_id"] is None


@pytest.mark.asyncio
async def test_audio_url_wins_over_a_connected_audio_asset(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id, asset_id="asset-image-1", asset_type="image", suffix=".png")
    _add_asset(manager, project_id, asset_id="asset-audio-1", asset_type="audio", suffix=".mp3")
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path=str(tmp_path / "out.mp4")))
    url = "https://cdn.example/song.wav"

    await manager.handle_director_generate(
        {
            "project_id": project_id,
            "mode": "image_audio2video",
            "reference_asset_ids": ["asset-image-1"],
            "input_audio_asset_id": "asset-audio-1",
            "input_audio_url": url,
            "prompt": "p",
        }
    )

    assert stub.calls[0]["kwargs"]["reference_audio_path"] == url


@pytest.mark.asyncio
async def test_generate_rejects_a_non_http_audio_url(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id, asset_id="asset-image-1", asset_type="image", suffix=".png")
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path=str(tmp_path / "out.mp4")))

    with pytest.raises(DirectorRpcError) as exc:
        await manager.handle_director_generate(
            {
                "project_id": project_id,
                "mode": "image_audio2video",
                "reference_asset_ids": ["asset-image-1"],
                "input_audio_url": "/Users/someone/song.wav",
                "prompt": "p",
            }
        )

    assert exc.value.code == "INVALID_PARAMS"
    assert "音频链接" in exc.value.message
    assert stub.calls == []


@pytest.mark.asyncio
async def test_generate_with_an_audio_url_does_not_need_a_prompt(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    """填了链接就是一次完整请求——空提示词不该被"提示词不能为空"拦住。"""
    _configure(monkeypatch)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id, asset_id="asset-image-1", asset_type="image", suffix=".png")
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path=str(tmp_path / "out.mp4")))

    result = await manager.handle_director_generate(
        {
            "project_id": project_id,
            "mode": "image_audio2video",
            "reference_asset_ids": ["asset-image-1"],
            "input_audio_url": "https://cdn.example/song.wav",
        }
    )

    assert len(stub.calls) == 1
    assert _asset_of(result)["type"] == "video"


@pytest.mark.asyncio
async def test_connected_audio_asset_is_uploaded_to_cloud_storage_when_connected(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    """云存储（azure-blob-storage）连上了的话，连线进来的音频素材要先传上去，
    用换回来的公网直链替代本地文件路径——绕开 generate_video 内联 base64 的
    体积上限，也省得用户自己再去对象存储里复制链接。"""
    _configure(monkeypatch)
    cloud_url = "https://examdatalake.blob.core.windows.net/examdatalake/audio/asset-audio-1.mp3"
    cloud_stub = _configure_cloud_storage(monkeypatch, url=cloud_url)
    project_id = await _create_project(manager)
    _add_asset(manager, project_id, asset_id="asset-image-1", asset_type="image", suffix=".png")
    audio_path = _add_asset(
        manager, project_id, asset_id="asset-audio-1", asset_type="audio", suffix=".mp3"
    )
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path=str(tmp_path / "out.mp4")))

    result = await _generate(manager, project_id, prompt="p")

    # 目标 key 固定为 <user_id>/<project_id>/<images|videos|audios>/<文件名>：
    # 目录结构本身就是多用户隔离的形态，连接器再按自己的前缀钳一次。
    assert len(cloud_stub.calls) == 1
    call = cloud_stub.calls[0]
    assert call["tool_name"] == "upload_file"
    assert call["arguments"]["local_path"] == audio_path
    expected_key = (
        f"{dm.get_local_user_id()}/{project_id}/audios/"
        f"{os.path.basename(audio_path)}"
    )
    assert call["arguments"]["blob_name"] == expected_key
    # The uploaded URL replaces the local path end to end: the tool call, and
    # the persisted asset's params (which the frontend rebuilds edges from).
    assert stub.calls[0]["kwargs"]["reference_audio_path"] == cloud_url
    asset = _asset_of(result)
    assert asset["params"]["input_audio_path"] == cloud_url
    # The asset id is still recorded - the uploaded URL doesn't erase which
    # connected asset it came from.
    assert asset["params"]["input_audio_asset_id"] == "asset-audio-1"


@pytest.mark.asyncio
async def test_falls_back_to_the_local_path_when_cloud_storage_is_not_connected(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    """没连云存储就按老路子走本地文件路径——云存储是可选加速路径，不是
    图音生视频的硬依赖。"""
    _configure(monkeypatch)  # defaults get_mcp_server_config to "not connected"
    project_id = await _create_project(manager)
    _add_asset(manager, project_id, asset_id="asset-image-1", asset_type="image", suffix=".png")
    audio_path = _add_asset(
        manager, project_id, asset_id="asset-audio-1", asset_type="audio", suffix=".mp3"
    )
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path=str(tmp_path / "out.mp4")))

    await _generate(manager, project_id, prompt="p")

    assert stub.calls[0]["kwargs"]["reference_audio_path"] == audio_path


@pytest.mark.asyncio
async def test_falls_back_to_the_local_path_when_the_cloud_upload_fails(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    """云存储连上了，但这一次上传本身失败（网络错误/认证过期等）——不该让
    整个生成跟着报错，退回本地文件路径照常生成。"""
    _configure(monkeypatch)
    _configure_cloud_storage(monkeypatch, error=RuntimeError("mcp upload failed: connection reset"))
    project_id = await _create_project(manager)
    _add_asset(manager, project_id, asset_id="asset-image-1", asset_type="image", suffix=".png")
    audio_path = _add_asset(
        manager, project_id, asset_id="asset-audio-1", asset_type="audio", suffix=".mp3"
    )
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path=str(tmp_path / "out.mp4")))

    result = await _generate(manager, project_id, prompt="p")

    assert stub.calls[0]["kwargs"]["reference_audio_path"] == audio_path
    assert _asset_of(result)["status"] == "ready"


@pytest.mark.asyncio
async def test_cloud_storage_upload_is_skipped_when_an_audio_url_is_given_directly(
    monkeypatch: pytest.MonkeyPatch, manager: DirectorManager, tmp_path: Path
):
    """手填的公网链接已经是直链了，不该再绕一圈去走云存储上传——那条路径
    只用于"连的是本地素材"的情况。"""
    _configure(monkeypatch)
    cloud_stub = _configure_cloud_storage(monkeypatch, url="https://unused.example/should-not-be-called.mp3")
    project_id = await _create_project(manager)
    _add_asset(manager, project_id, asset_id="asset-image-1", asset_type="image", suffix=".png")
    stub = _stub_video_tool(monkeypatch, _SAVED.format(path=str(tmp_path / "out.mp4")))
    url = "https://cdn.example/song.wav"

    await manager.handle_director_generate(
        {
            "project_id": project_id,
            "mode": "image_audio2video",
            "reference_asset_ids": ["asset-image-1"],
            "input_audio_url": url,
            "prompt": "p",
        }
    )

    assert cloud_stub.calls == []
    assert stub.calls[0]["kwargs"]["reference_audio_path"] == url
