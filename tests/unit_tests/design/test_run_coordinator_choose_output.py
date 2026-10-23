# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""RunCoordinator.choose_output: promoting / discarding single-node rerun candidates."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from jiuwenswarm.server.runtime.design.run_coordinator import RunCoordinator

_OLD = {"kind": "image", "uri": "file:///C:/p/old.png"}
_NEW = {"kind": "image", "uri": "file:///C:/p/new.png"}


class _Store:
    def __init__(self, run: dict[str, Any], graph: dict[str, Any]) -> None:
        self.runs = {run["run_id"]: run}
        self.graphs = {graph["graph_id"]: graph}

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        run = self.runs.get(run_id)
        return deepcopy(run) if run is not None else None

    def save_run(self, run: dict[str, Any]) -> dict[str, Any]:
        self.runs[run["run_id"]] = deepcopy(run)
        return run

    def get_graph(self, graph_id: str) -> dict[str, Any] | None:
        graph = self.graphs.get(graph_id)
        return deepcopy(graph) if graph is not None else None

    def save_graph(self, graph: dict[str, Any]) -> dict[str, Any]:
        self.graphs[graph["graph_id"]] = deepcopy(graph)
        return graph


def _setup(state: dict[str, Any]) -> tuple[_Store, RunCoordinator]:
    run = {"run_id": "r1", "graph_id": "g1", "status": "completed", "node_states": {"n_scene": state}}
    graph = {"graph_id": "g1", "nodes": [{"id": "n_scene", "type": "image", "output_ref": _OLD}], "edges": []}
    store = _Store(run, graph)
    return store, RunCoordinator(store, "g1")


def _image_candidate() -> dict[str, Any]:
    """What a single-node image rerun leaves behind: refs only, no candidate_text."""
    return {
        "status": "completed",
        "text": "old prompt",
        "output_ref": _OLD,
        "output_refs": [_OLD],
        "candidate_output_ref": _NEW,
        "candidate_output_refs": [_NEW],
        "history": [{"ts": 1, "text": "old prompt", "output_ref": _OLD}],
    }


def test_new_promotes_an_image_candidate_without_text() -> None:
    store, coordinator = _setup(_image_candidate())
    coordinator.choose_output("r1", "n_scene", "new")
    state = store.runs["r1"]["node_states"]["n_scene"]
    assert state["output_ref"] == _NEW
    assert state["output_refs"] == [_NEW], "the canvas reads output_refs first"
    assert state["text"] == "old prompt"
    assert state["history"][-1]["output_ref"] == _NEW
    assert not {"candidate_output_ref", "candidate_output_refs", "candidate_text"} & state.keys()
    assert store.graphs["g1"]["nodes"][0]["output_ref"] == _NEW


def test_refs_list_alone_is_a_candidate() -> None:
    state = _image_candidate()
    del state["candidate_output_ref"]
    store, coordinator = _setup(state)
    coordinator.choose_output("r1", "n_scene", "new")
    assert store.runs["r1"]["node_states"]["n_scene"]["output_ref"] == _NEW


def test_original_discards_every_candidate_field() -> None:
    store, coordinator = _setup(_image_candidate())
    coordinator.choose_output("r1", "n_scene", "original")
    state = store.runs["r1"]["node_states"]["n_scene"]
    assert state["output_ref"] == _OLD and state["output_refs"] == [_OLD]
    assert not {"candidate_output_ref", "candidate_output_refs", "candidate_text"} & state.keys()
    assert store.graphs["g1"]["nodes"][0]["output_ref"] == _OLD


def test_text_candidate_still_promotes_text() -> None:
    store, coordinator = _setup({"status": "completed", "text": "v1", "candidate_text": "v2"})
    coordinator.choose_output("r1", "n_scene", "new")
    state = store.runs["r1"]["node_states"]["n_scene"]
    assert state["text"] == "v2" and "candidate_text" not in state


def test_choosing_again_after_resolution_is_a_no_op() -> None:
    """The canvas may resend its auto-promotion (e.g. after 整理节点); that must not error."""
    store, coordinator = _setup(_image_candidate())
    coordinator.choose_output("r1", "n_scene", "new")
    before = deepcopy(store.runs["r1"])
    result = coordinator.choose_output("r1", "n_scene", "new")
    assert result["node_states"]["n_scene"]["output_ref"] == _NEW
    assert store.runs["r1"] == before


def test_unknown_node_or_run_is_still_an_error() -> None:
    _, coordinator = _setup(_image_candidate())
    with pytest.raises(ValueError, match="no rerun candidate"):
        coordinator.choose_output("r1", "n_missing", "new")
    with pytest.raises(ValueError, match="run not found"):
        coordinator.choose_output("r_missing", "n_scene", "new")
