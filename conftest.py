"""让 ``core`` 与 ``Github`` 两个包在测试里都能被导入。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ROOT 形如 .../Github，导入 core.* 需要它；导入 Github.* 需要它的父目录。
for _path in (ROOT.parent, ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
