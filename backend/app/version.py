"""後端版本字串（單一來源）。

發版時請與 frontend/package.json 的 version 一起更新。
"""

__version__ = "1.0.21"

# 授權條款（SPDX）。版本資訊頁會顯示它，所以它必須與 `backend/pyproject.toml`、
# `frontend/package.json` 與根目錄 `LICENSE` 一致 —— 授權改過一次
# （2026-08-15 Apache-2.0 → AGPL-3.0-or-later），四處只要有一處沒跟上，
# 畫面就會理直氣壯地顯示錯的授權。`tests/test_license_declaration.py` 綁住這四處。
__license__ = "AGPL-3.0-or-later"
