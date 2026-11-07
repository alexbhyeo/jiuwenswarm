# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``.tscn`` 场景解析与属性编辑.

Godot 的场景文件是纯文本格式，所以场景树不需要编辑器也能读：

```
[gd_scene load_steps=4 format=3]

[node name="Main" type="Control"]
script = ExtResource("1_main")

[node name="Background" type="TextureRect" parent="."]
```

这里把它解析成节点列表（含父子关系与属性），并支持**就地修改一个属性**：
只重写目标 ``[node ...]`` 小节里的那一行，其它字节原样保留——用户手写的
顺序、注释与空行都不会被我们的编辑器打乱。

设计取舍（D-20261107-1637 §5.3）：接上 Godot-MCP 后，场景读写优先走 RQL；
这个解析器是没连 MCP 时的可用实现，也是解析结果的校验基准。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_NODE_HEADER = re.compile(r"^\[node\b(?P<attrs>[^\]]*)\]", re.MULTILINE)
_ATTR = re.compile(r'(\w+)="([^"]*)"')
# Godot 里没有子节点的节点写在父节点下时 parent 指向父节点路径；
# 根节点的 parent 属性缺省。
_COMMENT_OR_EMPTY = re.compile(r"^\s*(;.*)?$")


@dataclass
class SceneNode:
    name: str
    type: str
    parent: str = ""
    properties: dict[str, str] = field(default_factory=dict)

    @property
    def path(self) -> str:
        # parent 缺省 = 场景根；parent="." 也是"挂在根下"，两者都归到根路径，
        # 具体根名由 build_tree 解析（根名不是固定的 "Main"）。
        if not self.parent or self.parent == ".":
            return self.name
        return f"{self.parent}/{self.name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "parent": self.parent,
            "path": self.path,
            "properties": dict(self.properties),
        }


def _parse_attrs(raw: str) -> dict[str, str]:
    return {key: value for key, value in _ATTR.findall(raw)}


def parse_scene(text: str) -> list[SceneNode]:
    """把 ``.tscn`` 文本解析成节点列表（保持文件顺序）."""
    headers = list(_NODE_HEADER.finditer(text))
    nodes: list[SceneNode] = []
    for index, match in enumerate(headers):
        attrs = _parse_attrs(match.group("attrs"))
        body_start = match.end()
        body_end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        properties: dict[str, str] = {}
        for line in text[body_start:body_end].splitlines():
            if _COMMENT_OR_EMPTY.match(line):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            properties[key.strip()] = value.strip()
        nodes.append(SceneNode(
            name=attrs.get("name", ""),
            type=attrs.get("type", ""),
            parent=attrs.get("parent", ""),
            properties=properties,
        ))
    return nodes


def build_tree(nodes: list[SceneNode]) -> dict[str, Any]:
    """按 parent 关系拼成 ``{name,type,path,properties,children[]}`` 的树.

    ``.tscn`` 里子节点的 ``parent="."`` 指的是**场景根**，而根名由用户决定
    （不一定是 ``Main``），所以先把根节点找出来，再把 ``"."`` 映射到它的路径。
    """
    root_name = next((n.name for n in nodes if not n.parent), "")
    index: dict[str, dict[str, Any]] = {}
    roots: list[dict[str, Any]] = []

    def resolve(parent: str) -> str:
        return root_name if parent in (".", "") else parent

    for node in nodes:
        parent_path = resolve(node.parent)
        entry = {**node.to_dict(), "children": []}
        entry["path"] = node.name if parent_path in ("", node.name) else f"{parent_path}/{node.name}"
        index[entry["path"]] = entry
        blocked = not node.parent or parent_path == node.name
        if not blocked and parent_path in index:
            index[parent_path]["children"].append(entry)
        else:
            roots.append(entry)
    return roots[0] if len(roots) == 1 else {"name": "", "children": roots}


def set_property(text: str, node_name: str, key: str, value: str, *, parent: str = "") -> str:
    """把 ``node_name`` 节点的 ``key`` 改成 ``value``，其它内容原样保留.

    找不到该节点、或该节点不唯一时抛 ``KeyError``（调用方转成 RPC 错误）。
    """
    matches = [
        match for match in _NODE_HEADER.finditer(text)
        if _parse_attrs(match.group("attrs")).get("name") == node_name
        and (not parent or _parse_attrs(match.group("attrs")).get("parent", "") == parent)
    ]
    if not matches:
        raise KeyError(f"未找到节点: {node_name}")
    if len(matches) > 1:
        raise KeyError(f"节点名不唯一，请提供 parent: {node_name}")

    match = matches[0]
    body_end = text.find("\n[", match.end())
    body_end = len(text) if body_end == -1 else body_end
    body = text[match.end():body_end]

    pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
    if pattern.search(body):
        new_body = pattern.sub(f"{key} = {value}", body, count=1)
    else:
        # 属性不存在：追加到该节点小节末尾（保留原有结尾换行）
        trimmed = body.rstrip("\n")
        new_body = f"{trimmed}\n{key} = {value}\n" if trimmed else f"\n{key} = {value}\n"
    return text[:match.end()] + new_body + text[body_end:]


def list_node_properties(root: Path, scene_path: str, node_name: str) -> dict[str, Any]:
    """读一个节点的类型与属性（供属性面板首次渲染）."""
    text = (root / scene_path).read_text(encoding="utf-8")
    for node in parse_scene(text):
        if node.name == node_name:
            return node.to_dict()
    raise KeyError(f"未找到节点: {node_name}")
