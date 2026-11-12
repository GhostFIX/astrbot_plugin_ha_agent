"""设置页（views/）与后端接口的契约测试。

浏览器在这个环境里跑不起来，所以用静态比对兜住最容易出错的几处：
字段名、单选值、接口路径、DOM id、Tab 结构、版本号。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from core.constants import PLUGIN_NAME, PLUGIN_VERSION, VIEW_NAME

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "views" / "settings" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "views" / "settings" / "app.js").read_text(encoding="utf-8")


def _js_array(name: str) -> list[str]:
    match = re.search(rf"const {name} = \[(.*?)\];", JS, re.S)
    assert match, f"{name} 未在 app.js 中定义"
    return re.findall(r'"([^"]+)"', match.group(1))


def _form_field_names() -> set[str]:
    return set(re.findall(r'name="([^"]+)"', HTML))


def test_every_referenced_field_exists_in_html():
    referenced: set[str] = set()
    for array in ("TEXT_FIELDS", "SECRET_FIELDS", "NUMBER_FIELDS"):
        referenced |= set(_js_array(array))
    referenced |= set(re.findall(r"form\.elements\.([A-Za-z_]+)", JS))
    referenced |= set(re.findall(r'checked\("([^"]+)"\)', JS))
    referenced |= set(re.findall(r'setRadio\("([^"]+)"', JS))

    missing = referenced - _form_field_names()
    assert not missing, f"app.js 引用了 HTML 中不存在的字段：{sorted(missing)}"


def test_multi_fields_have_pickers():
    fields = set(_js_array("MULTI_FIELDS"))
    pickers = set(re.findall(r'class="picker" data-field="([^"]+)"', HTML))
    assert fields == pickers, (fields, pickers)
    assert fields == {"notify_umos", "allowed_umos", "blocked_umos"}


def test_picker_markup_has_select_and_manual_input():
    blocks = re.split(r'class="picker"', HTML)
    found = 0
    for field in ("notify_umos", "allowed_umos", "blocked_umos"):
        marker = f'data-field="{field}"'
        block = next((b for b in blocks if marker in b), None)
        assert block, f"缺少 {field} 的 picker"
        found += 1
        for hook in ("data-chips", "data-select", "data-manual", "data-add-selected"):
            assert hook in block, f"{field} 缺少 {hook}"
    assert found == 3


def test_visibility_rules_match_radio_values():
    rules = set(re.findall(r'data-when="([^"]+)"', HTML))
    out_values = set(re.findall(r'name="outbound_transport" value="([^"]+)"', HTML))
    in_values = set(re.findall(r'name="inbound_transport" value="([^"]+)"', HTML))

    assert out_values == {"rest", "mqtt"}
    assert in_values == {"http", "mqtt", "off"}

    for rule in rules:
        if rule.startswith("outbound:"):
            assert rule.split(":", 1)[1] in out_values, rule
        elif rule.startswith("inbound:"):
            assert rule.split(":", 1)[1] in in_values, rule
        else:
            assert rule == "any:mqtt", rule


def test_plugin_id_matches_backend():
    assert f'const PLUGIN = "{PLUGIN_NAME}";' in JS


def test_endpoints_match_backend_routes():
    # 前端调用的是 bridge 的相对路径，后端注册的是 /<插件名>/<相对路径>
    calls = set(re.findall(r'api(?:Get|Post)\("([^"]+)"', JS))
    assert calls == {
        "settings",
        "settings/test",
        "settings/umos",
        "settings/token",
        "logs",
        "logs/clear",
    }

    backend = (ROOT / "core" / "web_api.py").read_text(encoding="utf-8")
    for endpoint in calls:
        assert f'f"/{{PLUGIN_NAME}}/{endpoint}"' in backend, endpoint


def test_version_is_consistent():
    init_version = re.search(
        r'__version__ = "([^"]+)"',
        (ROOT / "__init__.py").read_text(encoding="utf-8"),
    )
    meta_version = re.search(
        r"^version:\s*(\S+)",
        (ROOT / "metadata.yaml").read_text(encoding="utf-8"),
        re.M,
    )
    assert init_version and meta_version
    assert init_version.group(1) == PLUGIN_VERSION
    assert meta_version.group(1) == PLUGIN_VERSION


def test_view_has_entry_point_and_no_inline_script():
    assert '<script type="module" src="./app.js"></script>' in HTML
    # AstrBot 要求内联脚本改用外部 module 文件，这里不该出现内联脚本
    inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", HTML, re.S)
    assert all(not body.strip() for body in inline)


def test_tabs_have_matching_panels():
    tabs = set(re.findall(r'data-tab="([^"]+)"', HTML))
    panels = set(re.findall(r'data-panel="([^"]+)"', HTML))

    assert tabs == {"config", "logs"}
    assert panels == tabs
    assert "config" in JS and "logs" in JS
    assert 'switchTab(tab.dataset.tab)' in JS


def test_log_panel_is_a_table():
    assert "<table" in HTML and 'class="log-table"' in HTML
    assert 'id="log-rows"' in HTML and "<tbody" in HTML
    # 表头四列：时间 / 级别 / 来源 / 内容
    header = re.search(r"<thead>(.*?)</thead>", HTML, re.S)
    assert header
    for column in ("时间", "级别", "来源", "内容"):
        assert column in header.group(1), column
    # 表格渲染用 createElement，不再拼终端样式的字符串
    assert 'document.createElement("tr")' in JS
    assert "logRows.appendChild(row)" in JS


def test_long_umo_chips_cannot_blow_up_the_grid():
    """超长 UMO 是无空格长串，必须能在两列里收缩并省略。"""
    css = (ROOT / "views" / "settings" / "style.css").read_text(encoding="utf-8")

    assert "minmax(0, 1fr)" in css, "grid 列必须用 minmax(0,1fr)，否则 min-content 会撑破布局"
    assert ".chip-text" in css and "text-overflow: ellipsis" in css
    assert '"chip-text"' in JS, "chip 文本需要独立 span 才能生效省略号"
    assert ".grid2 > *" in css, "grid 子项需要 min-width: 0"


def test_token_is_managed_in_page_not_in_config_schema():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    assert "inbound_token" not in schema, "令牌不该再出现在配置里"
    assert 'name="inbound_token"' not in HTML, "页面里不该有可编辑的令牌输入框"

    for element in (
        "token-state",
        "token-rotate",
        "token-reveal",
        "token-copy",
        "token-value",
    ):
        assert f'id="{element}"' in HTML, element


def test_every_get_element_by_id_exists():
    ids = set(re.findall(r'document\.getElementById\("([^"]+)"\)', JS))
    html_ids = set(re.findall(r'id="([^"]+)"', HTML))

    assert ids, "脚本里没有 getElementById，检查是否写错"
    missing = ids - html_ids
    assert not missing, f"脚本引用了不存在的元素：{sorted(missing)}"


def test_only_one_view_dir_matching_view_name():
    views_dir = ROOT / "views"
    dirs = sorted(item.name for item in views_dir.iterdir() if item.is_dir())
    assert dirs == [VIEW_NAME], f"views 下应当只有 {VIEW_NAME}，实际：{dirs}"


def test_view_i18n_gives_a_readable_title():
    i18n_dir = ROOT / ".astrbot-plugin" / "i18n"
    files = sorted(i18n_dir.glob("*.json"))
    assert files, "缺少 i18n 文件，视图入口会显示目录名"

    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        title = data.get("views", {}).get(VIEW_NAME, {}).get("title")
        assert title, f"{path.name} 缺少 views.{VIEW_NAME}.title"
