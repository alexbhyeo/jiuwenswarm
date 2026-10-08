# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A reply must not announce output the run never produced.

The leader writes its summary before the run executes, so it describes the plan
as already achieved. ``replace_next_step`` only fixed the closing line, which is
why a chat turn could read "已根据分镜生成 @Character 1: 小象 的角色设定图 …
下一步是角色设定图" — the first sentence claimed an asset while the second
admitted the stage was still unbuilt, and no asset existed on the canvas.
"""

from __future__ import annotations

from typing import Any

import pytest

from jiuwenswarm.server.runtime.designer.leader_chat import (
    report_unbuilt_nodes,
    replace_next_step,
)


def _graph(*nodes: dict[str, Any]) -> dict[str, Any]:
    return {"graph_id": "graph_test", "project_id": "proj_test", "nodes": list(nodes)}


def _node(node_id: str, pipeline: str, *, uri: str | None = None) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "image" if pipeline in {"character_design", "scene"} else "video",
        "config": {"pipeline": pipeline},
        "output_ref": {"kind": "image", "uri": uri} if uri else None,
    }


_CLAIM = "已根据分镜生成 @Character 1: 小象 的角色设定图: 幼年非洲象。"


def test_built_node_keeps_the_summary() -> None:
    graph = _graph(_node("n_character", "character_design", uri="file:///tmp/c.png"))
    assert (
        report_unbuilt_nodes(
            _CLAIM, graph, ["n_character"], node_states={"n_character": {"status": "completed"}}, chinese=True
        )
        == _CLAIM
    )


def test_pending_node_replaces_the_claim() -> None:
    """The reported bug: prose claimed the sheet, the node had no output."""
    graph = _graph(_node("n_character", "character_design"))
    summary = report_unbuilt_nodes(
        _CLAIM, graph, ["n_character"], node_states={"n_character": {"status": "pending"}}, chinese=True
    )
    assert "已根据分镜生成" not in summary
    assert "角色设定图" in summary
    assert "未生成" in summary


def test_still_running_node_says_so() -> None:
    graph = _graph(_node("n_character", "character_design"))
    summary = report_unbuilt_nodes(
        _CLAIM,
        graph,
        ["n_character"],
        node_states={"n_character": {"status": "running"}},
        run_finished=False,
        chinese=True,
    )
    assert "仍在生成中" in summary
    assert "已根据分镜生成" not in summary


def test_failed_node_surfaces_its_error() -> None:
    graph = _graph(_node("n_image_1", "character_design"))
    summary = report_unbuilt_nodes(
        _CLAIM,
        graph,
        ["n_image_1"],
        node_states={"n_image_1": {"status": "failed", "error": "[ERROR]: image generation is switched off"}},
        chinese=True,
    )
    assert "[ERROR]: image generation is switched off" in summary


def test_only_the_unbuilt_nodes_are_reported() -> None:
    graph = _graph(
        _node("n_scene_1", "scene", uri="file:///tmp/s.png"),
        _node("n_character", "character_design"),
    )
    summary = report_unbuilt_nodes(
        _CLAIM,
        graph,
        ["n_scene_1", "n_character"],
        node_states={"n_scene_1": {"status": "completed"}, "n_character": {"status": "pending"}},
        chinese=True,
    )
    assert "角色设定图" in summary
    assert "场景设定图" not in summary


def test_english_wording() -> None:
    graph = _graph(_node("n_character", "character_design"))
    summary = report_unbuilt_nodes(
        "Generated the character sheet for @Character 1.",
        graph,
        ["n_character"],
        chinese=False,
    )
    assert summary.startswith("Not generated:")
    assert "the character sheet" in summary


def test_replace_next_step_keeps_the_truthful_message() -> None:
    """The corrected body must survive the next-step rewrite."""
    graph = _graph(_node("n_character", "character_design"))
    truthful = report_unbuilt_nodes(
        _CLAIM, graph, ["n_character"], node_states={"n_character": {"status": "pending"}}, chinese=True
    )
    final = replace_next_step(truthful, graph, chinese=True)
    assert "已根据分镜生成" not in final
    assert final.endswith("下一步是角色设定图。")


def test_unknown_node_id_is_ignored() -> None:
    graph = _graph(_node("n_character", "character_design", uri="file:///tmp/c.png"))
    assert (
        report_unbuilt_nodes(
            _CLAIM, graph, ["n_missing"], node_states={}, chinese=True
        )
        == _CLAIM
    )
