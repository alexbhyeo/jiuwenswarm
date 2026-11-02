"""Real end-to-end test of the EffectCraft/剪辑 integration, driving the exact
same HTTP/WS surface the real frontend uses (login, director.projects.create,
/file-api/director/upload, director.clip.effect.apply/check_status) against a
live jiuwenswarm deployment.

This is a smoke/demo test, not a pytest unit test: it talks to a *running*
server over the network (production or a local dev instance) and exercises
every effect in the v1 catalog (director_effects.EFFECT_CATALOG) against a
real uploaded asset, using each effect's own declared default parameters.
Keep this script and its fixtures - it doubles as a worked example of every
available effect's id, applicable asset types and parameters (see the
printed catalog dump and the final per-effect PASS/FAIL table).

Usage:
    python3 deploy/effects-worker/test_e2e.py <username> <password> [base_url]

    base_url defaults to https://happypeaceland.net. Pass e.g.
    http://localhost:5173 (and ws://.../ws is derived from it) to run against
    a local dev instance instead.

Requires: httpx, websockets (already project dependencies - run inside the
repo's venv: `source .venv/bin/activate`).
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import sys
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

import httpx
import websockets

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

# One representative fixture per asset type the catalog can target.
FIXTURES_BY_ASSET_TYPE = {
    "image": "test_image.png",
    "video": "test_video.mp4",
    "audio": "test_audio.wav",
}


def _ws_url(base_url: str) -> str:
    parts = urlsplit(base_url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    return urlunsplit((scheme, parts.netloc, "/ws", "", ""))


def _req_id() -> str:
    return f"e2etest_{uuid.uuid4().hex[:10]}"


async def ws_call(ws, method: str, params: dict, timeout: float = 30.0) -> dict:
    rid = _req_id()
    await ws.send(json.dumps({"type": "req", "id": rid, "method": method, "params": params}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
        msg = json.loads(raw)
        if msg.get("type") == "res" and msg.get("id") == rid:
            if not msg.get("ok", True) and msg.get("error"):
                raise RuntimeError(f"{method} failed: {msg.get('code')} {msg.get('error')}")
            return msg.get("payload") or {}
    raise TimeoutError(f"{method} timed out")


def upload_asset(client: httpx.Client, project_id: str, filename: str) -> tuple[str, str]:
    path = os.path.join(FIXTURES_DIR, filename)
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    with open(path, "rb") as fh:
        resp = client.post(
            "/file-api/director/upload",
            data={"project_id": project_id},
            files={"file": (filename, fh, mime)},
        )
    resp.raise_for_status()
    payload = resp.json()
    asset_id = payload["asset_id"]
    file_path = next(a["file_path"] for a in payload["project"]["assets"] if a["asset_id"] == asset_id)
    return asset_id, file_path


async def apply_and_wait(
    ws, client: httpx.Client, *, project_id: str, source_asset_id: str, source_file_path: str,
    effect_id: str, params: dict,
) -> dict:
    apply_payload = await ws_call(
        ws, "director.clip.effect.apply",
        {"project_id": project_id, "asset_id": source_asset_id, "effect_id": effect_id, "params": params},
    )
    new_asset_id = apply_payload["asset_id"]
    new_asset = next(a for a in apply_payload["project"]["assets"] if a["asset_id"] == new_asset_id)
    if new_asset["status"] != "pending" or not new_asset.get("job_id"):
        return {"status": new_asset["status"], "error": new_asset.get("error"), "file_path": new_asset.get("file_path")}
    job_id = new_asset["job_id"]

    status, final_asset = "pending", None
    for _ in range(30):
        check_payload = await ws_call(
            ws, "director.clip.effect.check_status",
            {"project_id": project_id, "asset_id": new_asset_id, "job_id": job_id},
        )
        status = check_payload["status"]
        final_asset = next(a for a in check_payload["project"]["assets"] if a["asset_id"] == new_asset_id)
        if status in ("ready", "failed"):
            break
        await asyncio.sleep(2)

    # The original asset must never be touched by applying an effect to it.
    get_payload = await ws_call(ws, "director.projects.get", {"project_id": project_id})
    orig = next(a for a in get_payload["project"]["assets"] if a["asset_id"] == source_asset_id)
    assert orig["file_path"] == source_file_path, f"original asset file_path changed! {orig['file_path']!r} != {source_file_path!r}"

    return {
        "status": status,
        "file_path": final_asset.get("file_path") if final_asset else None,
        "error": final_asset.get("error") if final_asset else None,
    }


async def main() -> None:
    username, password = sys.argv[1], sys.argv[2]
    base_url = sys.argv[3] if len(sys.argv) > 3 else "https://happypeaceland.net"
    ws_url = _ws_url(base_url)
    client = httpx.Client(base_url=base_url, timeout=30.0)

    print(f"==> Logging in as {username} at {base_url} ...")
    resp = client.post("/auth/login", json={"username": username, "password": password})
    resp.raise_for_status()
    cookie = resp.cookies.get("jiuwen_session")
    assert cookie, "no session cookie returned"
    client.cookies.set("jiuwen_session", cookie)
    print("    ok, session cookie acquired")

    async with websockets.connect(ws_url, additional_headers={"Cookie": f"jiuwen_session={cookie}"}) as ws:
        print("==> Creating test project ...")
        payload = await ws_call(ws, "director.projects.create", {"name": f"effect-e2e-{uuid.uuid4().hex[:6]}"})
        project_id = payload["project"]["project_id"]
        print(f"    project_id={project_id}")

        print("\n==> Fetching effect catalog (this is also the full list of what's available) ...")
        catalog = (await ws_call(ws, "director.clip.effects.list", {}))["effects"]
        for e in catalog:
            print(f"    {e['effect_id']:<16} [{', '.join(e['asset_types']):<16}] params={[p['key'] for p in e['params']]}")

        # One upload per asset type we actually need, reused across every
        # effect that targets that type - no point re-uploading per effect.
        needed_types = {e["asset_types"][0] for e in catalog}
        uploads: dict[str, tuple[str, str]] = {}
        for asset_type in needed_types:
            filename = FIXTURES_BY_ASSET_TYPE[asset_type]
            asset_id, file_path = upload_asset(client, project_id, filename)
            uploads[asset_type] = (asset_id, file_path)
            print(f"\n==> Uploaded {filename} as {asset_type} asset_id={asset_id}")

        print("\n==> Applying every effect in the catalog, with its own declared defaults ...")
        results: dict[str, dict] = {}
        for e in catalog:
            asset_type = e["asset_types"][0]  # the primary/first-listed type is enough to exercise the effect
            source_asset_id, source_file_path = uploads[asset_type]
            default_params = {p["key"]: p["default"] for p in e["params"]}
            print(f"\n--- {e['effect_id']} on a {asset_type} asset, params={default_params} ---")
            result = await apply_and_wait(
                ws, client,
                project_id=project_id, source_asset_id=source_asset_id, source_file_path=source_file_path,
                effect_id=e["effect_id"], params=default_params,
            )
            print(f"    -> {result}")
            results[e["effect_id"]] = result

        print("\n==> Cleaning up test project ...")
        await ws_call(ws, "director.projects.delete", {"project_id": project_id})
        print("    deleted")

        print("\n=== RESULTS ===")
        all_ok = True
        for effect_id, r in results.items():
            ok = r["status"] == "ready" and r["file_path"]
            all_ok = all_ok and ok
            print(f"  {'PASS' if ok else 'FAIL'}  {effect_id:<16} {r}")
        print("\nOVERALL:", "PASS" if all_ok else "FAIL")


if __name__ == "__main__":
    asyncio.run(main())
