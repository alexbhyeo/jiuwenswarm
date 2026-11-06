# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""把本地素材换成服务商能抓取的公网 HTTPS 直链.

视频生视频、图音生视频这类卡片的输入要由服务商自己去下载，只认 HTTPS 直链：
本地文件路径、``data:`` URI 都会被拒。素材默认落在本地磁盘上，所以这里把它
传进对象存储再签一条有时效的直链出来。

支持的存储，按配置自动选择：

- OBS / MinIO（``OBS_URL`` / ``OBS_ACCESS_KEY`` / ``OBS_SECRET_KEY`` /
  ``OBS_BUCKET``）：复用 ``agent_gateway.common.obs_files.AttachmentStore``，
  上传与签名都由它负责。
- 腾讯云 COS（``TENCENT_COS_SECRET_ID`` / ``TENCENT_COS_SECRET_KEY`` /
  ``TENCENT_COS_REGION`` / ``TENCENT_COS_DEFAULT_BUCKET``）：桶默认私有，
  直链访问会 403，因此一律用预签名 URL。

两者都没配、或上传/签名失败时返回 ``None``，调用方据此回退到原来的报错或本地
路径——云存储是"有就用"的可选加速项，不该让生成直接失败。

对象按内容寻址（``<prefix>/<sha256 前 16 位><扩展名>``），同一份文件只上传
一次；进程内再缓存一层 URL，避免每次生成都重新上传。
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# 服务商通常在上传后几秒内抓取；给足余量又不至于签出长期有效的链接。
_URL_TTL_SECONDS = 6 * 60 * 60
_KEY_PREFIX = "director-inputs"
_MAX_UPLOAD_BYTES = 200 * 1024 * 1024

_cache: dict[str, tuple[int, int, str]] = {}
_cache_lock = threading.Lock()


def object_key_for_asset(asset_id: str, suffix: str) -> str:
    """素材在对象存储里的 key：一条素材一个对象，删除时才能精确对应."""
    return f"{_KEY_PREFIX}/{asset_id}{suffix.lower()}"


def _obs_url(path: Path, key: str) -> str | None:
    if not all(os.getenv(name, "").strip() for name in ("OBS_URL", "OBS_ACCESS_KEY", "OBS_SECRET_KEY")):
        return None
    try:
        from agent_gateway.common.obs_files import AttachmentStore

        store = AttachmentStore.from_env()
        store.upload_file(path, key)
        return store.presign_download(key)
    except Exception as exc:  # noqa: BLE001 - 存储不可用不该阻断生成
        logger.warning("[director] OBS 上传失败（%s）：%s", path.name, exc)
        return None


def _cos_url(path: Path, key: str) -> str | None:
    required = (
        "TENCENT_COS_SECRET_ID",
        "TENCENT_COS_SECRET_KEY",
        "TENCENT_COS_REGION",
        "TENCENT_COS_DEFAULT_BUCKET",
    )
    if not all(os.getenv(name, "").strip() for name in required):
        return None
    try:
        from qcloud_cos import CosConfig, CosS3Client
    except ImportError:
        logger.warning("[director] 已配置腾讯云 COS 但缺少 cos-python-sdk-v5，跳过上传")
        return None
    try:
        config = CosConfig(
            Region=os.environ["TENCENT_COS_REGION"],
            SecretId=os.environ["TENCENT_COS_SECRET_ID"],
            SecretKey=os.environ["TENCENT_COS_SECRET_KEY"],
            Scheme="https",
        )
        client = CosS3Client(config)
        bucket = os.environ["TENCENT_COS_DEFAULT_BUCKET"]
        client.upload_file(
            Bucket=bucket, Key=key, LocalFilePath=str(path), PartSize=1, MAXThread=2,
        )
        # 桶是私有的，直链会 403，必须签一条带时效的下载链接。
        return client.get_presigned_url(
            Bucket=bucket, Key=key, Method="GET", Expired=_URL_TTL_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[director] COS 上传失败（%s）：%s", path.name, exc)
        return None


def publish_asset(
    local_path: str | os.PathLike[str] | None, asset_id: str = "",
) -> tuple[str, str] | None:
    """上传素材并签名，返回 ``(公网直链, 对象 key)``；做不到时返回 ``None``.

    key 按素材 id 生成（而不是按内容哈希），这样素材删除时能精确删掉它自己的
    对象，不会误删别处还在用的同内容文件。
    """
    if not local_path:
        return None
    path = Path(str(local_path))
    if not path.is_file():
        return None
    try:
        info = path.stat()
    except OSError:
        return None
    if info.st_size <= 0 or info.st_size > _MAX_UPLOAD_BYTES:
        return None

    key = object_key_for_asset(asset_id or hashlib.sha256(str(path).encode()).hexdigest()[:16], path.suffix)
    cache_key = key
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and cached[0] == info.st_size and cached[1] == int(info.st_mtime):
            return cached[2], key

    url = _obs_url(path, key) or _cos_url(path, key)
    if not url:
        return None
    with _cache_lock:
        _cache[cache_key] = (info.st_size, int(info.st_mtime), url)
    logger.info("[director] 素材 %s 已换成公网直链（key=%s）", path.name, key)
    return url, key


def delete_public_object(key: str) -> bool:
    """尽力删除对象存储里的对象；没配存储或删除失败都返回 False."""
    if not key:
        return False
    removed = False
    if all(os.getenv(name, "").strip() for name in ("OBS_URL", "OBS_ACCESS_KEY", "OBS_SECRET_KEY")):
        try:
            from agent_gateway.common.obs_files import AttachmentStore

            AttachmentStore.from_env().client.remove_object(
                AttachmentStore.from_env().bucket, key,
            )
            removed = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[director] OBS 删除对象失败（%s）：%s", key, exc)
    if not removed:
        required = ("TENCENT_COS_SECRET_ID", "TENCENT_COS_SECRET_KEY",
                    "TENCENT_COS_REGION", "TENCENT_COS_DEFAULT_BUCKET")
        if all(os.getenv(name, "").strip() for name in required):
            try:
                from qcloud_cos import CosConfig, CosS3Client

                client = CosS3Client(CosConfig(
                    Region=os.environ["TENCENT_COS_REGION"],
                    SecretId=os.environ["TENCENT_COS_SECRET_ID"],
                    SecretKey=os.environ["TENCENT_COS_SECRET_KEY"],
                    Scheme="https",
                ))
                client.delete_object(Bucket=os.environ["TENCENT_COS_DEFAULT_BUCKET"], Key=key)
                removed = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("[director] COS 删除对象失败（%s）：%s", key, exc)
    if removed:
        with _cache_lock:
            _cache.pop(key, None)
        logger.info("[director] 已删除对象存储里的素材对象 key=%s", key)
    return removed
