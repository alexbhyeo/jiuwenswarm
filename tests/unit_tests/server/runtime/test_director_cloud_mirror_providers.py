import asyncio
from unittest.mock import patch
import pytest
from jiuwenswarm.server.runtime.director import director_manager as dm


def test_mirror_installs_providers_when_process_has_none(tmp_path, mocker):
    """WebChannel 进程没装配注入点：镜像前必须自己装一次，否则静默跳过。"""
    f = tmp_path / "a.png"
    f.write_bytes(b"x")
    install = mocker.patch(
        "jiuwenswarm.server.runtime.provider_setup.install_front"
    )
    cfg = mocker.patch.object(dm, "get_mcp_server_config", return_value=None)

    assert asyncio.run(dm.mirror_asset_to_cloud_storage(
        file_path=str(f), asset_type="image", project_id="p1")) is None
    install.assert_called_once()
    # 两个候选连接器都探过一遍，都没配才放弃
    assert [c.args[0] for c in cfg.call_args_list] == [
        "tencent-cos", "azure-blob-storage",
    ]


def test_mirror_skips_non_existent_file_without_installing(tmp_path, mocker):
    install = mocker.patch(
        "jiuwenswarm.server.runtime.provider_setup.install_front"
    )
    assert asyncio.run(dm.mirror_asset_to_cloud_storage(
        file_path=str(tmp_path / "missing.png"), asset_type="image",
        project_id="p1")) is None
    install.assert_not_called()


def test_mirror_uploads_when_connector_visible(tmp_path, mocker):
    f = tmp_path / "a.png"
    f.write_bytes(b"x")
    mocker.patch("jiuwenswarm.server.runtime.provider_setup.install_front")
    mocker.patch.object(dm, "get_local_user_id", return_value="u1")
    entry = {"name": "tencent-cos", "transport": "stdio"}
    mocker.patch.object(dm, "get_mcp_server_config", side_effect=lambda n: entry if n == "tencent-cos" else None)
    calls = {}

    async def fake_call(e, tool, args, timeout_s=0):
        calls.update(entry=e, tool=tool, args=args)
        return "https://cos.example/u1/p1/images/a.png"

    mocker.patch.object(dm, "call_mcp_tool", side_effect=fake_call)

    url = asyncio.run(dm.mirror_asset_to_cloud_storage(
        file_path=str(f), asset_type="image", project_id="p1"))
    assert url == "https://cos.example/u1/p1/images/a.png"
    assert calls["tool"] == "upload_file"
    assert calls["args"] == {"local_path": str(f), "object_key": "u1/p1/images/a.png"}


def test_mirror_provider_install_failure_does_not_raise(tmp_path, mocker):
    f = tmp_path / "a.png"
    f.write_bytes(b"x")
    mocker.patch(
        "jiuwenswarm.server.runtime.provider_setup.install_front",
        side_effect=RuntimeError("boom"),
    )
    mocker.patch.object(dm, "get_mcp_server_config", return_value=None)
    assert asyncio.run(dm.mirror_asset_to_cloud_storage(
        file_path=str(f), asset_type="image", project_id="p1")) is None
