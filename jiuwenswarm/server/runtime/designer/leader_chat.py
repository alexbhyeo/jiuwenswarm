# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Invisible Designer leader: chat-driven graph edits and output refine."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from jiuwenswarm.common.schema.designer_graph import (
    ACTIVITY_KIND_STAGE,
    ACTIVITY_KIND_THINKING,
    ACTIVITY_KIND_TOOL_CALL,
    NODE_TYPE_IMAGE,
    NODE_TYPE_VIDEO,
    DesignerExecutionGraph,
    DesignerGraphNode,
    apply_graph_patch,
    node_pipeline,
    utc_now_ms,
)

logger = logging.getLogger(__name__)

ProgressFn = Callable[..., None]

_RUN_HINT = re.compile(
    r"(生成|重跑|重生成|运行|合成|拼接|剪成|成片|出片|run\b|generate|rerun|regenerate"
    r"|compose|stitch|concatenate|final cut)",
    re.I,
)
_REFINE_HINT = re.compile(
    r"(改|更|精修|refine|more |make |变成|换成|prompt|规格|brief|storyboard|分镜)",
    re.I,
)
_ADD_HINT = re.compile(
    r"(加|添加|新增|add |new |删|去掉|remove|delete|connect|接到|连到)",
    re.I,
)
_CONNECT_HINT = re.compile(r"(接到|连到|connect(?:\s+to)?)", re.I)
_VIDEO_HINT = re.compile(r"(视频|镜头|clip|video)", re.I)

_LEADER_SYSTEM = """You are the invisible Designer Leader. Reply with a JSON object only.
Canvas node type and config.role must be one of: text, table, image, video, audio.
Character/Scene/Keyframe/Clip/Film are pipelines, never node kinds.
Do not rebuild the whole graph. Patch only what the user asked.
Do not create audio nodes. Audio generation is not implemented; users add and upload audio on the canvas.
Do not wire a new node into clip/compose unless the user asked to connect it.
user_canvas_edits is the user's canvas log: add, remove, connect, disconnect, replace.
Treat that log as fact. Do not recreate a removed node, restore a disconnected edge,
or undo a replaced output. Do not connect an added node unless the user asked.
connect and disconnect name node_id and peer_id. replace names the node whose output the user changed.
recent_conversation (if present) is the last few chat turns — use it for context (e.g. a short
follow-up like "make it brighter" refers back to whatever node you two were just discussing).
The user's message may reference an existing node by "@Label" (the node's own label, e.g.
"@Character 1"). When it does, that node's current output image has been attached to this request
so you can see it — keep that same subject/style/identity when you create or refine a node in
response, and mention the "@Label" you used for continuity in your "summary" the same way.
Any attached images at the end of this request (from an "@Label" mention or a file the user
uploaded) are the visual ground truth — describe new/updated node prompts in terms of what you see
in them rather than restating a generic description.

Schema:
{
  "intent": "edit_graph" | "refine_node" | "answer",
  "summary": "short user-facing Chinese or English summary",
  "thinking": "one-line peek of what you are doing",
  "patch": {
    "upsert_nodes": [],
    "upsert_edges": [],
    "remove_node_ids": [],
    "remove_edge_ids": []
  },
  "prompt_updates": [{"node_id": "", "prompt": ""}],
  "identity_updates": [{"node_id": "", "character_id": "", "description": ""}],
  "run_node_ids": []
}

Rules:
- edit_graph: change topology. Leave run_node_ids empty unless the user asked to generate/run.
- refine_node: update that node's config.prompt (and brief/storyboard text if asked). Put the target in run_node_ids so it regenerates.
- A character/person node carries its real identity (what it looks like — color, costume,
  build, etc.) in config.costume_lock and the story's cast list, NOT in config.prompt — a
  character or appearance change (e.g. "change @Character 1's color to white") MUST also be
  given in identity_updates (node_id, that node's config.character_id, and a short plain-fact
  description of the NEW appearance only — no wrapper phrasing, just the visual facts, same
  language as the existing description) or the regenerated image will keep the OLD appearance
  no matter what prompt_updates says.
- answer: no patch, just summary.
- Only the node(s) listed in run_node_ids are regenerated — downstream scenes and clips are NOT
  rebuilt. Changing one asset (e.g. a character image) must never be treated as a request to redo
  the rest of the film, and a freshly generated asset is never an invitation to continue: do not
  add downstream ids on your own.
- Never ask the user to confirm a generated image or clip. Do not write "角色图确认后…",
  "场景图确认后，下一步…", "分镜确认后…" or any "需要我继续吗？" / "shall I continue?" question.
  State what was produced and name the natural next stage as a plain statement, e.g.
  "角色图已生成，下一步是场景设定图。" / "The character sheet is done; the next step is the scene
  set." The user drives each step themselves and will ask in chat when they want a redo or a
  refinement — do not solicit confirmation.
"""


_DONT_RUN = re.compile(
    r"(先别|不要跑|不要生成|别生成|不用跑|without running|don'?t run|do not run)",
    re.I,
)


def message_asks_to_run(message: str, *, run_new_nodes: bool = False) -> bool:
    text = str(message or "")
    if _DONT_RUN.search(text):
        return False
    if run_new_nodes:
        return True
    return bool(_RUN_HINT.search(text))


def _emit(progress: ProgressFn | None, kind: str, text: str, tool: str = "") -> None:
    if not callable(progress):
        return
    try:
        progress(kind, text, tool)
    except TypeError:
        progress(kind, text)


def _node_by_id(graph: DesignerExecutionGraph, node_id: str) -> DesignerGraphNode | None:
    target = str(node_id or "").strip()
    if not target:
        return None
    for node in graph.get("nodes") or []:
        if str(node.get("id") or "") == target:
            return node
    return None


def _match_node(graph: DesignerExecutionGraph, message: str) -> DesignerGraphNode | None:
    text = str(message or "").strip().lower()
    if not text:
        return None
    ranked: list[tuple[int, DesignerGraphNode]] = []
    for node in graph.get("nodes") or []:
        node_id = str(node.get("id") or "")
        label = str(node.get("label") or "")
        score = 0
        if node_id and node_id.lower() in text:
            score += 3
        if label and label.lower() in text:
            score += 2
        if score:
            ranked.append((score, node))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1] if ranked else None


_AT_LABEL = re.compile(r"@([^\s@][^\n]*?)(?=(?:\s@|[,，。.!！?？;；]|\s{2}|$))")

_MAX_LABEL_REFERENCE_IMAGES = 3


def resolve_label_references(graph: DesignerExecutionGraph, message: str) -> list[str]:
    """Resolve "@Label" mentions in ``message`` to that node's output image.

    Longest-label-first so e.g. "@Character 1" isn't shadowed by a shorter
    "@Character" match. Only image-kind outputs are usable as a vision
    reference; a mention of a text/table/video/audio node, or a node with no
    output yet, is silently skipped rather than erroring the whole turn.
    """
    text = str(message or "")
    if "@" not in text:
        return []
    by_label: dict[str, DesignerGraphNode] = {}
    for node in graph.get("nodes") or []:
        label = str(node.get("label") or "").strip()
        if label:
            by_label[label] = node
    if not by_label:
        return []
    ordered_labels = sorted(by_label, key=len, reverse=True)
    sources: list[str] = []
    seen_ids: set[str] = set()
    for match in _AT_LABEL.finditer(text):
        candidate = match.group(1).strip()
        label = next((lbl for lbl in ordered_labels if candidate.startswith(lbl)), None)
        if not label:
            continue
        node = by_label[label]
        node_id = str(node.get("id") or label)
        if node_id in seen_ids:
            continue
        output_ref = node.get("output_ref")
        if not isinstance(output_ref, dict):
            continue
        if str(output_ref.get("kind") or "") != NODE_TYPE_IMAGE:
            continue
        uri = str(output_ref.get("uri") or "").strip()
        if not uri:
            continue
        seen_ids.add(node_id)
        sources.append(uri)
        if len(sources) >= _MAX_LABEL_REFERENCE_IMAGES:
            break
    return sources


def _next_label(graph: DesignerExecutionGraph, node_type: str) -> str:
    count = sum(1 for node in graph.get("nodes") or [] if str(node.get("type") or "") == node_type)
    title = node_type[:1].upper() + node_type[1:]
    return f"{title} {count + 1}"


def _next_node_id(graph: DesignerExecutionGraph, prefix: str) -> str:
    used = {str(node.get("id") or "") for node in graph.get("nodes") or []}
    if prefix not in used:
        return prefix
    index = 2
    while f"{prefix}_{index}" in used:
        index += 1
    return f"{prefix}_{index}"


def _place_right(graph: DesignerExecutionGraph) -> dict[str, float]:
    max_x = 40.0
    y = 240.0
    for node in graph.get("nodes") or []:
        layout = node.get("layout") or {}
        x = float(layout.get("x") or 0)
        if x >= max_x:
            max_x = x
            y = float(layout.get("y") or y)
    return {"x": max_x + 368, "y": y, "width": 280, "height": 160}


def _compose_or_sink_id(graph: DesignerExecutionGraph) -> str | None:
    ids = {str(node.get("id") or "") for node in graph.get("nodes") or []}
    for candidate in ("n_compose", "n_final"):
        if candidate in ids:
            return candidate
    for node in reversed(list(graph.get("nodes") or [])):
        if str(node.get("type") or "") == NODE_TYPE_VIDEO:
            return str(node.get("id") or "") or None
    return None



def _merge_prompt_updates(graph: DesignerExecutionGraph, plan: dict[str, Any]) -> dict[str, Any]:
    patch = dict(plan.get("patch") or {})
    updates = plan.get("prompt_updates") or []
    if not isinstance(updates, list) or not updates:
        return patch
    upsert = list(patch.get("upsert_nodes") or [])
    by_id = {str(item.get("id") or ""): dict(item) for item in upsert if isinstance(item, dict)}
    for item in updates:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("node_id") or "").strip()
        prompt = str(item.get("prompt") or "").strip()
        if not node_id or not prompt:
            continue
        node = by_id.get(node_id) or (_node_by_id(graph, node_id) and dict(_node_by_id(graph, node_id) or {}))
        if not node:
            continue
        cfg = dict(node.get("config") or {})
        cfg["prompt"] = prompt
        node["config"] = cfg
        by_id[node_id] = node
    if by_id:
        patch["upsert_nodes"] = list(by_id.values())
    return patch


_IDENTITY_OVERRIDE_MARKER = "USER-REQUESTED IDENTITY CHANGE (authoritative, not a stale field):"


def _apply_identity_updates(
    graph: DesignerExecutionGraph,
    next_graph: DesignerExecutionGraph,
    plan: dict[str, Any],
) -> DesignerExecutionGraph:
    """A character node's real look lives in config.costume_lock + the cast list in
    graph.metadata.script_analysis, not config.prompt — leaf agents read those, so an
    appearance change has to land there too or regeneration keeps the old look."""
    updates = plan.get("identity_updates") or []
    if not isinstance(updates, list) or not updates:
        return next_graph
    nodes_by_id = {str(n.get("id") or ""): dict(n) for n in next_graph.get("nodes") or []}
    meta = dict(next_graph.get("metadata") or {})
    script_analysis = dict(meta.get("script_analysis") or {})
    characters = [dict(c) for c in (script_analysis.get("characters") or []) if isinstance(c, dict)]
    chars_by_id = {str(c.get("id") or ""): c for c in characters if c.get("id")}
    nodes_changed = False
    meta_changed = False
    for item in updates:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("node_id") or "").strip()
        description = str(item.get("description") or "").strip()
        node = nodes_by_id.get(node_id)
        if not node or not description:
            continue
        cfg = dict(node.get("config") or {})
        name = str(cfg.get("character_name") or item.get("character_id") or "").strip()
        costume_lock = f"{name}: {description}" if name else description
        cfg["costume_lock"] = costume_lock
        director_task = str(cfg.get("director_task") or "").strip()
        if director_task and _IDENTITY_OVERRIDE_MARKER in director_task:
            director_task = director_task.split(_IDENTITY_OVERRIDE_MARKER, 1)[0].rstrip()
        if director_task:
            cfg["director_task"] = (
                f"{director_task}\n\n{_IDENTITY_OVERRIDE_MARKER} {costume_lock}\n"
                "This is the user's deliberate, just-given instruction for THIS character, "
                "given through chat moments ago. It outranks anything you read via read_upstream "
                "(brief, storyboard, PRODUCTION LOCK BIBLE, other clips' costume_lock) or the "
                "character's own name/label that still says otherwise — those have not been "
                "regenerated yet and describe the OLD appearance. Use the appearance stated here, "
                "not the old one, even though other sources you read still disagree with it."
            )
        node["config"] = cfg
        nodes_by_id[node_id] = node
        nodes_changed = True
        # Prefer the node's own config.character_id (ground truth) over whatever id the
        # LLM guessed in identity_updates — the LLM sometimes fabricates a plausible-looking
        # id ("character_1") that doesn't match the story's real id ("char_1").
        char_id = str(cfg.get("character_id") or item.get("character_id") or "").strip()
        character = chars_by_id.get(char_id)
        if character is not None:
            character["description"] = description
            character["costume_lock"] = costume_lock
            attrs = character.get("identity_attrs")
            if isinstance(attrs, dict) and "wardrobe" in attrs:
                attrs = dict(attrs)
                attrs["wardrobe"] = costume_lock
                character["identity_attrs"] = attrs
            meta_changed = True
        if char_id:
            # Every other node featuring this same character (other scenes/clips) carries
            # its own copy of costume_lock too — leave those stale and a regen there (or
            # even this one, via read_upstream) can see a conflict and side with the old
            # majority text instead of the just-requested change.
            for other_id, other in nodes_by_id.items():
                if other_id == node_id:
                    continue
                other_cfg = other.get("config")
                if not isinstance(other_cfg, dict):
                    continue
                other_char_id = str(other_cfg.get("character_id") or "").strip()
                other_char_ids = [str(x) for x in (other_cfg.get("character_ids") or [])]
                if char_id != other_char_id and char_id not in other_char_ids:
                    continue
                other_cfg = dict(other_cfg)
                other_cfg["costume_lock"] = costume_lock
                other["config"] = other_cfg
                nodes_by_id[other_id] = other
    if not nodes_changed and not meta_changed:
        return next_graph
    patched = dict(next_graph)
    if nodes_changed:
        patched["nodes"] = list(nodes_by_id.values())
    if meta_changed:
        script_analysis["characters"] = characters
        if str(script_analysis.get("production_bible") or "").strip():
            from jiuwenswarm.server.runtime.designer.pipeline.production_bible import (
                build_production_bible,
            )

            script_analysis["production_bible"] = build_production_bible(
                script_analysis, user_prompt=str(script_analysis.get("summary") or "")
            )
        meta["script_analysis"] = script_analysis
        patched["metadata"] = meta
    return patched


def apply_leader_plan(
    graph: DesignerExecutionGraph,
    plan: dict[str, Any],
) -> tuple[DesignerExecutionGraph, list[str], str]:
    intent = str(plan.get("intent") or "answer").strip() or "answer"
    summary = str(plan.get("summary") or "").strip()
    patch = _merge_prompt_updates(graph, plan)
    has_patch = any(patch.get(key) for key in ("upsert_nodes", "upsert_edges", "remove_node_ids", "remove_edge_ids"))
    next_graph = apply_graph_patch(graph, patch) if has_patch else graph
    next_graph = _apply_identity_updates(graph, next_graph, plan)
    raw_run_ids = plan.get("run_node_ids") or []
    run_ids = [str(item).strip() for item in raw_run_ids if str(item).strip()]
    if intent != "refine_node":
        # Topology edits only run when the plan explicitly listed ids.
        run_ids = run_ids
    known = {str(node.get("id") or "") for node in next_graph.get("nodes") or []}
    run_ids = [item for item in run_ids if item in known]
    if not summary:
        if intent == "refine_node":
            summary = "Updated the selected node."
        elif has_patch:
            summary = "Updated the workflow graph."
        else:
            summary = "No graph changes."
    return next_graph, run_ids, summary


def _sanitize_plan(plan: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(plan, dict):
        return {"intent": "answer", "summary": "Could not understand that request.", "patch": {}, "run_node_ids": []}
    intent = str(plan.get("intent") or "answer").strip()
    if intent not in {"edit_graph", "refine_node", "answer"}:
        intent = "answer"
    patch = plan.get("patch") if isinstance(plan.get("patch"), dict) else {}
    run_ids = plan.get("run_node_ids") if isinstance(plan.get("run_node_ids"), list) else []
    prompt_updates = plan.get("prompt_updates") if isinstance(plan.get("prompt_updates"), list) else []
    identity_updates = plan.get("identity_updates") if isinstance(plan.get("identity_updates"), list) else []
    return {
        "intent": intent,
        "summary": str(plan.get("summary") or "").strip(),
        "thinking": str(plan.get("thinking") or "").strip(),
        "patch": patch,
        "prompt_updates": prompt_updates,
        "identity_updates": identity_updates,
        "run_node_ids": [str(item).strip() for item in run_ids if str(item).strip()],
    }


async def _llm_leader_plan(
    graph: DesignerExecutionGraph,
    message: str,
    *,
    selected_node_id: str = "",
    history: list[dict[str, str]] | None = None,
    images: list[str] | None = None,
) -> dict[str, Any]:
    from jiuwenswarm.server.runtime.designer.model_tools import (
        DesignerLlmError,
        LLM_API_ERROR,
        call_model_tool,
        model_text_or_raise,
    )
    from jiuwenswarm.server.runtime.designer.script_analysis import _extract_json_object

    meta = graph.get("metadata") if isinstance(graph.get("metadata"), dict) else {}
    snapshot: dict[str, Any] = {
        "selected_node_id": selected_node_id,
        "user_canvas_edits": list(meta.get("user_canvas_edits") or [])[-20:],
        "nodes": [
            {
                "id": node.get("id"),
                "type": node.get("type"),
                "label": node.get("label"),
                "pipeline": node_pipeline(node),
                "prompt": str((node.get("config") or {}).get("prompt") or "")[:240],
            }
            for node in graph.get("nodes") or []
        ],
        "edges": [
            {"id": edge.get("id"), "source": edge.get("source"), "target": edge.get("target")}
            for edge in graph.get("edges") or []
        ],
        "user": message,
    }
    if history:
        # Last few turns only — this is context for a short follow-up, not a
        # transcript; keeps the snapshot small and avoids re-litigating old asks.
        snapshot["recent_conversation"] = [
            {"role": str(item.get("role") or "user"), "content": str(item.get("content") or "")[:600]}
            for item in history[-8:]
            if str(item.get("content") or "").strip()
        ]
    try:
        result = await call_model_tool(
            prompt=json.dumps(snapshot, ensure_ascii=False),
            system=_LEADER_SYSTEM,
            optimize_for="quality",
            max_tokens=16384,
            images=images or None,
        )
        text = model_text_or_raise(result)
    except DesignerLlmError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.info("Leader chat model call failed", exc_info=True)
        raise DesignerLlmError(
            f"Chat model request failed while planning canvas edits: {exc}",
            code=LLM_API_ERROR,
        ) from exc
    parsed = _extract_json_object(text)
    plan = _sanitize_plan(parsed)
    if plan.get("intent") == "answer" and not str(plan.get("summary") or "").strip():
        raise DesignerLlmError(
            "Chat model did not return a usable canvas edit plan.",
            code=LLM_API_ERROR,
        )
    return plan


async def run_leader_chat(
    graph: DesignerExecutionGraph,
    message: str,
    *,
    selected_node_id: str = "",
    run_new_nodes: bool = False,
    progress: ProgressFn | None = None,
    history: list[dict[str, str]] | None = None,
    attached_images: list[str] | None = None,
) -> dict[str, Any]:
    text = str(message or "").strip()
    _emit(progress, ACTIVITY_KIND_THINKING, "reading the canvas and your request")
    # "@Label" mentions (an existing node's own label) resolve to that node's
    # output image so the model sees it, same spirit as a file the user
    # attached directly — both just become vision references for this turn.
    label_images = resolve_label_references(graph, text)
    images = [*label_images, *(attached_images or [])][:_MAX_LABEL_REFERENCE_IMAGES]
    plan = await _llm_leader_plan(
        graph, text, selected_node_id=selected_node_id, history=history, images=images or None
    )
    thinking = str(plan.get("thinking") or "applying graph edits")
    _emit(progress, ACTIVITY_KIND_THINKING, thinking)
    # An edit_graph plan may only execute nodes when the message asked to run;
    # "继续合成" resolves through _RUN_HINT, so a compose request keeps its
    # run_node_ids instead of being silently emptied into a no-op.
    if plan.get("intent") == "edit_graph" and not message_asks_to_run(
        text, run_new_nodes=run_new_nodes
    ):
        plan["run_node_ids"] = []
    if plan.get("intent") == "refine_node" and not plan.get("run_node_ids") and selected_node_id:
        plan["run_node_ids"] = [selected_node_id]

    _emit(progress, ACTIVITY_KIND_TOOL_CALL, "designer_graph_patch", tool="designer_graph_patch")
    next_graph, run_ids, summary = apply_leader_plan(graph, plan)
    changed = next_graph is not graph and next_graph.get("updated_at") != graph.get("updated_at")
    if not changed:
        # apply_graph_patch always writes updated_at; compare node/edge identity.
        changed = (next_graph.get("nodes") != graph.get("nodes")) or (
            next_graph.get("edges") != graph.get("edges")
        )
    result = {
        "intent": plan.get("intent"),
        "summary": summary,
        "graph": next_graph,
        "run_node_ids": run_ids,
        "changed": changed or bool(plan.get("prompt_updates")) or bool(plan.get("identity_updates")),
        "updated_at": utc_now_ms(),
    }
    _emit(progress, ACTIVITY_KIND_STAGE, summary or "done", tool="")
    return result
