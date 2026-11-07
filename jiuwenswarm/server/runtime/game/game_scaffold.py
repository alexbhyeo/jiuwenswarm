# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""2D 主城工程脚手架（森林冒险形态）.

对着设计稿里那张竖屏手游主城：顶部资源条、中间森林小镇场景、五个入口
（探索 / 灵宠 / 背包 / 驻守 / 商店）、底部灵宠卡队列。这里生成的是一个
**真实可运行的 Godot 4 2D 工程**：打开即可看到主城，点击底部按钮切换面板，
再点一次收起。

生成出来的图（角色/场景/道具）由 ``generate_visual`` 产出后登记为项目素材，
脚手架按类型把它们接进场景：``scene`` 铺成背景，``character`` 立绘站在
主城中央，其余作为面板里的图标。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

PROJECT_GODOT = """\
; Engine configuration file.
; 由 WorkSwarm 游戏工坊生成；可用 Godot 4.3+ 直接打开运行。

config_version=5

[application]

config/name="{name}"
run/main_scene="res://scenes/main.tscn"
config/features=PackedStringArray("4.3", "GL Compatibility")

[display]

window/size/viewport_width=720
window/size/viewport_height=1280
window/stretch/mode="canvas_items"
window/stretch/aspect="keep"

[rendering]

renderer/rendering_method="gl_compatibility"
renderer/rendering_method.mobile="gl_compatibility"
"""

MAIN_SCENE = """\
[gd_scene load_steps={load_steps} format=3]

[ext_resource type="Script" path="res://scripts/main.gd" id="1_main"]
{texture_resources}

[node name="Main" type="Control"]
layout_mode = 3
anchors_preset = 15
anchor_right = 1.0
anchor_bottom = 1.0
script = ExtResource("1_main")

[node name="Background" type="TextureRect" parent="."]
layout_mode = 1
anchors_preset = 15
anchor_right = 1.0
anchor_bottom = 1.0
expand_mode = 1
stretch_mode = 6
{background_texture}

[node name="Title" type="Label" parent="."]
layout_mode = 1
offset_left = 40.0
offset_top = 40.0
offset_right = 680.0
offset_bottom = 96.0
theme_override_font_sizes/font_size = 40
text = "{name}"
horizontal_alignment = 1

[node name="Resources" type="Label" parent="."]
layout_mode = 1
offset_left = 40.0
offset_top = 110.0
offset_right = 680.0
offset_bottom = 156.0
theme_override_font_sizes/font_size = 26
text = "⚡ 60/120    🪙 15,680    💎 1,280"
horizontal_alignment = 1

[node name="Hero" type="TextureRect" parent="."]
layout_mode = 1
offset_left = 240.0
offset_top = 520.0
offset_right = 480.0
offset_bottom = 760.0
expand_mode = 1
stretch_mode = 5
{hero_texture}

[node name="PetRow" type="HBoxContainer" parent="."]
layout_mode = 1
offset_left = 40.0
offset_top = 880.0
offset_right = 680.0
offset_bottom = 1080.0
theme_override_constants/separation = 16

[node name="MenuBar" type="HBoxContainer" parent="."]
layout_mode = 1
offset_left = 40.0
offset_top = 1120.0
offset_right = 680.0
offset_bottom = 1220.0
theme_override_constants/separation = 12
"""

MAIN_SCRIPT = """\
extends Control

## 主城：底部五个入口切换面板；再点一次当前入口收起。

const ENTRIES := [
\t{"key": "explore", "label": "探索", "desc": "进入森林，寻找灵宠与资源。"},
\t{"key": "pets", "label": "灵宠", "desc": "查看已收集的灵宠与等级。"},
\t{"key": "bag", "label": "背包", "desc": "整理道具、材料与装备。"},
\t{"key": "guard", "label": "驻守", "desc": "派驻灵宠守卫小镇，按小时产出资源。"},
\t{"key": "shop", "label": "商店", "desc": "用金币与钻石兑换道具。"},
]

var _active := ""
var _panel: PanelContainer
var _panel_label: Label


func _ready() -> void:
	print("[game] 主城就绪：入口 %d 个，视口 %s" % [ENTRIES.size(), str(get_viewport_rect().size)])
	_build_menu()
	_build_panel()
	if "--capture" in OS.get_cmdline_user_args():
		_capture_and_quit()


func _capture_and_quit() -> void:
	# 只在带 --capture 运行时截图：用于构建后的可视化验收，不影响正常游玩。
	await get_tree().process_frame
	await get_tree().process_frame
	await get_tree().process_frame
	var image := get_viewport().get_texture().get_image()
	var path := "user://capture.png"
	image.save_png(path)
	print("[game] capture saved: ", ProjectSettings.globalize_path(path))
	get_tree().quit()


func _build_menu() -> void:
\tvar bar := get_node_or_null("MenuBar")
\tif bar == null:
\t\treturn
\tfor entry in ENTRIES:
\t\tvar button := Button.new()
\t\tbutton.text = entry["label"]
\t\tbutton.custom_minimum_size = Vector2(0, 80)
\t\tbutton.size_flags_horizontal = Control.SIZE_EXPAND_FILL
\t\tbutton.pressed.connect(_on_entry_pressed.bind(entry))
\t\tbar.add_child(button)


func _build_panel() -> void:
\t_panel = PanelContainer.new()
\t_panel.set_anchors_preset(Control.PRESET_CENTER)
\t_panel.position = Vector2(90, 300)
\t_panel.custom_minimum_size = Vector2(540, 220)
\t_panel.visible = false
\tvar margin := MarginContainer.new()
\tmargin.add_theme_constant_override("margin_left", 24)
\tmargin.add_theme_constant_override("margin_top", 24)
\tmargin.add_theme_constant_override("margin_right", 24)
\tmargin.add_theme_constant_override("margin_bottom", 24)
\t_panel_label = Label.new()
\t_panel_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
\t_panel_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
\tmargin.add_child(_panel_label)
\t_panel.add_child(margin)
\tadd_child(_panel)


func _on_entry_pressed(entry: Dictionary) -> void:
\tvar key: String = entry["key"]
\tif _active == key:
\t\t_active = ""
\t\t_panel.visible = false
\t\treturn
\t_active = key
\t_panel_label.text = "%s\\n\\n%s" % [entry["label"], entry["desc"]]
\t_panel.visible = true
\tprint("[game] 打开面板: ", entry["label"])
"""


def _texture_resource(relative: str, resource_id: str) -> str:
    return f'[ext_resource type="Texture2D" path="res://{relative}" id="{resource_id}"]'


def _pick(assets: Iterable[Any], asset_type: str) -> Any | None:
    for asset in assets:
        if getattr(asset, "type", None) == asset_type and getattr(asset, "file_path", None):
            return asset
    return None


def _copy_into_project(source: str, project_dir: Path, subdir: str) -> str | None:
    """把生成出来的图复制进工程 ``assets/<subdir>/``，返回仓库相对路径."""
    import shutil

    src = Path(source)
    if not src.is_file():
        return None
    target_dir = project_dir / "assets" / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / src.name
    if not target.exists():
        shutil.copy2(src, target)
    return target.relative_to(project_dir).as_posix()


def scaffold_home_town(project_dir: Path, name: str, assets: Iterable[Any]) -> list[str]:
    """生成 2D 主城工程；返回本次新建的相对路径列表（幂等）."""
    created: list[str] = []
    project_dir.mkdir(parents=True, exist_ok=True)

    background_asset = _pick(assets, "scene")
    hero_asset = _pick(assets, "character")
    background_rel = (
        _copy_into_project(background_asset.file_path, project_dir, "backgrounds")
        if background_asset
        else None
    )
    hero_rel = (
        _copy_into_project(hero_asset.file_path, project_dir, "characters")
        if hero_asset
        else None
    )

    resources: list[str] = []
    if background_rel:
        resources.append(_texture_resource(background_rel, "2_bg"))
    if hero_rel:
        resources.append(_texture_resource(hero_rel, "3_hero"))
    texture_block = "\n".join(resources)
    scene = MAIN_SCENE.format(
        load_steps=2 + len(resources),
        texture_resources=texture_block,
        background_texture=(
            'texture = ExtResource("2_bg")' if background_rel else ""
        ),
        hero_texture=('texture = ExtResource("3_hero")' if hero_rel else ""),
        name=name,
    )

    files = {
        "project.godot": PROJECT_GODOT.format(name=name),
        "scenes/main.tscn": scene,
        "scripts/main.gd": MAIN_SCRIPT,
    }
    for relative, content in files.items():
        target = project_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # 场景文件随素材变化需要重写；脚本与配置已存在时保留用户改动。
        if target.exists() and relative != "scenes/main.tscn":
            continue
        target.write_text(content, encoding="utf-8")
        created.append(relative)

    for directory in ("assets", "assets/backgrounds", "assets/characters", "addons"):
        (project_dir / directory).mkdir(parents=True, exist_ok=True)

    logger.info(
        "[game] 2D 主城脚手架完成 %s（背景=%s 立绘=%s，新建 %d 个文件）",
        project_dir.name,
        background_rel or "无",
        hero_rel or "无",
        len(created),
    )
    return created
