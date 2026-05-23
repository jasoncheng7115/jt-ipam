"""Plugin 機制（Phase 4）。

第三方套件可透過 entry_points 註冊：
  [project.entry-points."jt_ipam.plugins"]
  my_plugin = "my_pkg.plugin:plugin"

plugin 物件需是 JtIpamPlugin 實例：
    from app.plugins import JtIpamPlugin
    plugin = JtIpamPlugin(
        name="my-plugin",
        version="1.0.0",
        on_load=lambda app: app.include_router(my_router),
    )

啟動時 main.py 會呼叫 load_plugins() 自動掃 entry_points 並執行 on_load。

OWASP A03：plugin 是受信任程式碼（pip install 階段已過 SBOM 審核），不做
signature 驗證；但 plugin 不能繞過 RBAC（應該用 jt-ipam 提供的 dependencies）。
"""

from app.plugins.registry import (
    JtIpamPlugin,
    PluginInfo,
    list_plugins,
    load_plugins,
)

__all__ = ["JtIpamPlugin", "PluginInfo", "list_plugins", "load_plugins"]
