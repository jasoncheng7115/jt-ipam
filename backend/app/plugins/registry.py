"""Plugin 註冊器：discover via entry_points + 提供啟動 hook。"""

from __future__ import annotations

import importlib.metadata as _md
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

import structlog

if TYPE_CHECKING:
    from fastapi import FastAPI

ENTRY_POINT_GROUP = "jt_ipam.plugins"

_log = structlog.get_logger("plugins")


@dataclass
class PluginInfo:
    name: str
    version: str
    description: str | None = None
    error: str | None = None  # 載入失敗時的錯誤訊息


@dataclass
class JtIpamPlugin:
    """Plugin 物件需提供的最小介面。"""

    name: str
    version: str = "0.0.0"
    description: str | None = None
    # 應用啟動時呼叫；可在此 include_router、註冊 GraphQL types、開排程等
    on_load: Callable[["FastAPI"], None] | None = None
    # 應用關閉時呼叫
    on_shutdown: Callable[["FastAPI"], None] | None = None


_loaded: list[PluginInfo] = field(default_factory=list)  # type: ignore[arg-type]
_plugins: list[PluginInfo] = []


def list_plugins() -> list[PluginInfo]:
    """已載入的 plugin 清單（含載入失敗者）。"""
    return list(_plugins)


def load_plugins(app) -> list[PluginInfo]:  # type: ignore[no-untyped-def]
    """掃 entry_points，載入並呼叫 on_load。"""
    _plugins.clear()
    try:
        eps = _md.entry_points(group=ENTRY_POINT_GROUP)
    except TypeError:
        # 老版 importlib.metadata 行為
        eps = _md.entry_points().get(ENTRY_POINT_GROUP, [])  # type: ignore[assignment]
    for ep in eps:
        info = PluginInfo(name=ep.name, version="?")
        try:
            plugin = ep.load()
            if not isinstance(plugin, JtIpamPlugin):
                info.error = f"entry_point did not return JtIpamPlugin (got {type(plugin).__name__})"
                _plugins.append(info)
                _log.warning("plugin_invalid", name=ep.name, error=info.error)
                continue
            info.name = plugin.name
            info.version = plugin.version
            info.description = plugin.description
            if plugin.on_load is not None:
                plugin.on_load(app)
            _plugins.append(info)
            _log.info("plugin_loaded", name=info.name, version=info.version)
        except Exception as exc:  # noqa: BLE001
            info.error = f"{exc.__class__.__name__}: {exc}"
            _plugins.append(info)
            _log.error("plugin_load_failed", name=ep.name, error=str(exc))
    return _plugins
