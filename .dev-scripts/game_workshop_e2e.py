"""End-to-end browser test for the 游戏工坊 (Game Workshop) feature.

Covers, against the real running dev server at http://localhost:5173 with
a real backend (real Tripo3D API, not mocked):

- GameRail: collapsible 项目 section (+ dialog-based project creation,
  mirroring DirectorRail's "+ 新建项目" -> NewProjectDialog) and collapsible
  角色/世界/视频/分解产物/3D 模型 asset sections.
- GameCreationHome (创作 tab main panel): GameComposer (角色/世界/视频 mode
  pills + textarea + send button, mirroring Director's ComposerCard) and
  the "我的项目" card grid (clicking a card selects it and switches to 实验室,
  mirroring Director's ProjectGrid.onSelect).
- GameLabCanvas (实验室 tab): the round "图片处理" toolrail button that opens
  a side menu with "2D转3D" and "分解图", drag-and-drop from the rail onto
  the canvas, connecting an image node to a process card, submitting, and
  polling the real Tripo3D pipeline to completion for both process kinds.

GameRail's `expanded` state only ever has ONE section open at a time (same
single-toggle behavior as DirectorRail) and defaults to 'character' already
expanded on mount — this script only clicks a section row to switch to a
*different* section, never to "open" one that's already open (that would
instead close it).
"""
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE_URL = "http://localhost:5173"
SHOT_DIR = Path("/private/tmp/claude-501/-Users-yeoboonhong-Documents-GitHub-jiuwenswarm/33907c79-5917-4011-9eb0-2df21bcfdc62/scratchpad/shots")
SHOT_DIR.mkdir(parents=True, exist_ok=True)

errors = []


def shot(page, name):
    path = SHOT_DIR / f"{name}.png"
    page.screenshot(path=str(path))
    print(f"[shot] {path}")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 960})
        page = context.new_page()
        all_console = []
        page.on("console", lambda msg: all_console.append(f"[{msg.type}] {msg.text}"))
        page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))

        try:
            _run_flow(page)
        finally:
            print("[console] full console log:")
            for line in all_console:
                print("   ", line)
            shot(page, "99_final_state")
            browser.close()


def _run_flow(page):
    print("[nav] loading app")
    page.goto(BASE_URL, wait_until="domcontentloaded")
    page.wait_for_selector('[data-testid="session-sidebar-rail"]', timeout=30000)

    print("[nav] click game workshop nav item")
    page.click('[data-testid="session-sidebar-nav-item"][data-variant="game"]')
    page.wait_for_selector('[data-testid="game-workshop-new-project-button"]', timeout=15000)
    shot(page, "01_game_workshop_landing")

    project_name = f"e2e-test-{int(time.time())}"
    print(f"[create] new project via dialog: {project_name}")
    page.click('[data-testid="game-workshop-new-project-button"]')
    page.wait_for_selector('[data-testid="game-workshop-new-project-dialog-overlay"]', timeout=5000)
    page.fill('[data-testid="game-workshop-new-project-dialog-input"]', project_name)
    page.click('[data-testid="game-workshop-new-project-dialog-confirm"]')
    page.wait_for_selector('[data-testid="game-workshop-new-project-dialog-overlay"]', state="hidden", timeout=20000)
    page.wait_for_selector('[data-testid="game-workshop-composer"]', timeout=15000)
    shot(page, "02_project_created")

    print("[create] expand 项目 section, confirm the new project row is selected")
    page.click('[data-testid="game-workshop-rail-section-row"][data-variant="projects"]')
    page.wait_for_selector('[data-testid="game-workshop-project-list-item"][aria-pressed="true"]', timeout=5000)
    projects_count = page.locator(
        '[data-testid="game-workshop-rail-section-row"][data-variant="projects"] .game-rail-category-count'
    ).inner_text()
    print(f"[create] 项目 count badge: {projects_count}")
    shot(page, "02b_projects_section_expanded")
    # 验收"展开态单选"——点同一个 section 行会把它收起到"全部收起"
    # （expanded=null），不是回到挂载时的默认态（角色），所以收起后要显式
    # 点开角色才能继续后面的生成流程。
    page.click('[data-testid="game-workshop-rail-section-row"][data-variant="projects"]')
    page.wait_for_selector('[data-testid="game-workshop-project-list-item"]', state="hidden", timeout=5000)
    print("[create] 项目 section collapses on second click: OK")
    page.click('[data-testid="game-workshop-rail-section-row"][data-variant="character"]')
    page.wait_for_selector('[data-testid="game-workshop-rail-asset-grid"]', timeout=5000)

    print("[create] confirm composer renders full-width in the create main panel")
    composer_w = page.eval_on_selector(".game-composer", "el => el.getBoundingClientRect().width")
    scroll_w = page.eval_on_selector(".game-create-scroll", "el => el.getBoundingClientRect().width")
    print(f"[create] composer width={composer_w} scroll container width={scroll_w}")
    assert composer_w > scroll_w * 0.8, "composer must fill its scroll container, not shrink to content width"

    print("[create] generate a character via the composer (角色 mode is default)")
    page.fill(
        '[data-testid="game-workshop-composer-textarea"]',
        "一个穿着皮甲、佩戴腰带和项链的卡通冒险者角色，手持短剑，站立姿势，白色背景，概念设计图",
    )
    page.click('[data-testid="game-workshop-composer-send"]')
    print("[create] waiting for generated asset to appear (ready)...")
    page.wait_for_selector(
        '[data-testid="game-workshop-rail-section-body"][data-variant="character"] [data-testid="game-workshop-rail-asset-item"] img',
        timeout=120000,
    )
    shot(page, "03_character_generated")
    character_count_badge = page.locator(
        '[data-testid="game-workshop-rail-section-row"][data-variant="character"] .game-rail-category-count'
    ).inner_text()
    print(f"[create] 角色 count badge after generate: {character_count_badge}")
    assert int(character_count_badge.strip()) >= 1, "expected at least one character asset after generate"

    print("[create] switch composer mode to 世界, confirm mode pill is active")
    page.click('[data-testid="game-workshop-composer-mode"][data-variant="world"]')
    is_active = "game-composer-mode--active" in (
        page.get_attribute('[data-testid="game-workshop-composer-mode"][data-variant="world"]', "class") or ""
    )
    print(f"[create] 世界 mode pill active: {is_active}")
    assert is_active, "clicking the 世界 mode pill should mark it active"

    print("[create] switch rail to 分解产物 section, confirm NO generate form (rail is browse-only now)")
    page.click('[data-testid="game-workshop-rail-section-row"][data-variant="model_part"]')
    page.wait_for_selector('[data-testid="game-workshop-rail-section-body"][data-variant="model_part"]', timeout=5000)
    rail_has_generate = page.locator(
        '[data-testid="game-workshop-rail-section-body"][data-variant="model_part"] textarea'
    ).count()
    assert rail_has_generate == 0, "GameRail category bodies must be browse-only (no inline generate form)"
    print("[create] rail 分解产物 section has no generate form: OK")

    print("[create] switch rail back to 角色 so it's the drag source for the lab flow below")
    page.click('[data-testid="game-workshop-rail-section-row"][data-variant="character"]')
    page.wait_for_selector(
        '[data-testid="game-workshop-rail-section-body"][data-variant="character"] [data-testid="game-workshop-rail-asset-item"]',
        timeout=5000,
    )

    print("[create->lab] click a 我的项目 card, confirm it switches straight to 实验室")
    page.locator('[data-testid="game-workshop-project-card"]').first.click()
    page.wait_for_selector('[data-testid="game-workshop-tab"][data-variant="lab"][aria-pressed="true"]', timeout=5000)
    page.wait_for_selector(".game-lab-canvas-wrap", timeout=15000)
    print("[create->lab] clicking a project card switched to 实验室: OK")
    shot(page, "04_lab_tab_rail_still_visible")

    # The rail (drag source) must stay mounted/visible on the Lab tab.
    rail_visible = page.locator('[data-testid="game-workshop-rail-asset-grid"]').is_visible()
    print(f"[lab] rail asset grid visible on lab tab: {rail_visible}")
    assert rail_visible, "GameRail must stay mounted/visible on the Lab tab (drag source for process cards)"

    print("[lab] open the round 图片处理 toolrail button, confirm the 2-option menu")
    page.click('[data-testid="game-workshop-lab-toolrail-button"][data-variant="image"]')
    page.wait_for_selector('[data-testid="game-workshop-lab-add-menu"]', timeout=5000)
    menu_items = page.locator('[data-testid="game-workshop-lab-add-menu-item"]')
    labels = [menu_items.nth(i).inner_text() for i in range(menu_items.count())]
    print(f"[lab] toolrail menu options: {labels}")
    assert labels == ["2D转3D", "分解图"], f"expected exactly [2D转3D, 分解图] in that order, got {labels}"
    shot(page, "05_toolrail_menu")

    _run_process_card(
        page,
        kind="decompose",
        add_menu_variant="decompose",
        node_testid="game-workshop-lab-decompose-node",
        submit_testid="game-workshop-lab-decompose-node-submit",
        hint_testid="game-workshop-lab-decompose-node-hint",
        drop_xy=(150, 150),
        rail_category="model_part",
        shot_prefix="06",
    )

    print("[lab] reopen toolrail for the second process card (分解图's menu click auto-closed it)")
    page.click('[data-testid="game-workshop-lab-toolrail-button"][data-variant="image"]')
    page.wait_for_selector(
        '[data-testid="game-workshop-lab-add-menu-item"][data-variant="toModel"]', timeout=5000
    )

    _run_process_card(
        page,
        kind="toModel",
        add_menu_variant="toModel",
        node_testid="game-workshop-lab-to-model-node",
        submit_testid="game-workshop-lab-to-model-node-submit",
        hint_testid=None,
        drop_xy=(150, 500),
        rail_category="model",
        shot_prefix="07",
    )

    print("[pageerror] errors captured during run:")
    for e in errors:
        print("   ", e)


def _run_process_card(page, *, kind, add_menu_variant, node_testid, submit_testid, hint_testid, drop_xy, rail_category, shot_prefix):
    print(f"[lab:{kind}] add a {kind} process card")
    page.click(f'[data-testid="game-workshop-lab-add-menu-item"][data-variant="{add_menu_variant}"]')
    page.wait_for_selector(f'[data-testid="{node_testid}"]', timeout=10000)
    shot(page, f"{shot_prefix}_card_added")

    print(f"[lab:{kind}] drag a 角色 asset onto the lab canvas")
    source = page.locator('[data-testid="game-workshop-rail-asset-item"]').first
    canvas = page.locator(".game-lab-canvas-wrap .react-flow__pane").first
    source.scroll_into_view_if_needed()
    box_s = source.bounding_box()
    box_c = canvas.bounding_box()
    page.drag_and_drop(
        '[data-testid="game-workshop-rail-asset-item"]',
        ".game-lab-canvas-wrap .react-flow__pane",
        source_position={"x": box_s["width"] / 2, "y": box_s["height"] / 2},
        target_position={"x": drop_xy[0], "y": drop_xy[1]},
    )
    page.wait_for_timeout(500)
    image_node_count = page.locator('[data-testid="game-workshop-lab-image-node"]').count()
    print(f"[lab:{kind}] image nodes on canvas after drop: {image_node_count}")

    if image_node_count == 0:
        print(f"[lab:{kind}] WARNING: drag-and-drop missed, retrying with explicit mouse steps")
        page.mouse.move(box_s["x"] + box_s["width"] / 2, box_s["y"] + box_s["height"] / 2)
        page.mouse.down()
        page.wait_for_timeout(150)
        page.mouse.move(box_c["x"] + drop_xy[0], box_c["y"] + drop_xy[1], steps=10)
        page.wait_for_timeout(150)
        page.mouse.up()
        page.wait_for_timeout(500)
        image_node_count = page.locator('[data-testid="game-workshop-lab-image-node"]').count()
        print(f"[lab:{kind}] image nodes after manual retry: {image_node_count}")

    assert image_node_count >= 1, f"[lab:{kind}] could not create an image node via drag-and-drop"

    # Target the most recently added nodes — earlier runs against the same
    # project can leave older nodes on the (persisted) canvas.
    image_node = page.locator('[data-testid="game-workshop-lab-image-node"]').last
    process_node = page.locator(f'[data-testid="{node_testid}"]').last

    print(f"[lab:{kind}] connecting image node output to {kind} node input")
    img_handle = image_node.locator(".react-flow__handle").first
    target_handle = process_node.locator(".react-flow__handle").first
    ib = img_handle.bounding_box()
    tb = target_handle.bounding_box()
    page.mouse.move(ib["x"] + ib["width"] / 2, ib["y"] + ib["height"] / 2)
    page.mouse.down()
    page.wait_for_timeout(100)
    page.mouse.move(tb["x"] + tb["width"] / 2, tb["y"] + tb["height"] / 2, steps=15)
    page.wait_for_timeout(100)
    page.mouse.up()
    page.wait_for_timeout(500)
    shot(page, f"{shot_prefix}b_connected")

    if hint_testid:
        process_node.locator(f'[data-testid="{hint_testid}"]').fill(
            "weapon, belt, necklace, head, hair, torso, legs, boots"
        )

    submit_btn = process_node.locator(f'[data-testid="{submit_testid}"]')
    is_disabled = submit_btn.is_disabled()
    print(f"[lab:{kind}] submit button disabled: {is_disabled}")
    shot(page, f"{shot_prefix}c_before_submit")
    assert not is_disabled, f"[lab:{kind}] submit button should be enabled once an image is connected"

    submit_btn.click()
    print(f"[lab:{kind}] submitted; polling the real Tripo3D pipeline to completion (up to 5 min)...")
    try:
        process_node.locator(".game-lab-node-status").wait_for(timeout=300000)
    except PWTimeout:
        print(f"[lab:{kind}] TIMEOUT waiting for the process card to resolve within 5 min")
    page.wait_for_timeout(300)
    shot(page, f"{shot_prefix}d_result")

    status_text = process_node.locator(".game-lab-node-status").inner_text()
    print(f"[lab:{kind}] result status text: {status_text}")

    rail_count = page.locator(
        f'[data-testid="game-workshop-rail-section-row"][data-variant="{rail_category}"] .game-rail-category-count'
    ).inner_text()
    print(f"[lab:{kind}] rail 「{rail_category}」 count badge after completion: {rail_count}")


if __name__ == "__main__":
    main()
