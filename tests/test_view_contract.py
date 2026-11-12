"""设置页（views/）与后端接口的契约测试。

浏览器在这个环境里跑不起来，所以用静态比对兜住最容易出错的几处：
字段名、单选值、接口路径、插件名、版本号。
"""

from __future__ import annotations

import re
from pathlib import Path

from core.constants import PLUGIN_NAME, PLUGIN_VERSION

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "views" / "settings" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "views" / "settings" / "app.js").read_text(encoding="utf-8")
LOGS_HTML = (ROOT / "views" / "logs" / "index.html").read_text(encoding="utf-8")
LOGS_JS = (ROOT / "views" / "logs" / "app.js").read_text(encoding="utf-8")


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
    assert calls == {"settings", "settings/test", "settings/umos"}

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


def test_every_get_element_by_id_exists():
    for html, js in ((HTML, JS), (LOGS_HTML, LOGS_JS)):
        ids = set(re.findall(r'document\.getElementById\("([^"]+)"\)', js))
        html_ids = set(re.findall(r'id="([^"]+)"', html))
        assert ids, "脚本里没有 getElementById，检查是否写错"
        missing = ids - html_ids
        assert not missing, f"脚本引用了不存在的元素：{sorted(missing)}"


def test_logs_view_uses_same_plugin_id():
    assert f'const PLUGIN = "{PLUGIN_NAME}";' in LOGS_JS


def test_logs_view_endpoints_match_backend():
    calls = set(re.findall(r'api(?:Get|Post)\("([^"]+)"', LOGS_JS))
    assert calls == {"logs", "logs/clear"}

    backend = (ROOT / "core" / "web_api.py").read_text(encoding="utf-8")
    for endpoint in calls:
        assert f'f"/{{PLUGIN_NAME}}/{endpoint}"' in backend, endpoint


def test_logs_view_has_entry_point():
    assert '<script type="module" src="./app.js"></script>' in LOGS_HTML
