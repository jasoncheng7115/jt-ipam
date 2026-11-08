"""機櫃示意圖的伺服器端 SVG。

畫面上的機櫃圖是前端畫的（`utils/rackGraphicsExport.ts`），但要讓**別的系統**嵌入
（LibreNMS dashboard 的 widget 之類）就必須有一個純網址能拿到圖 —— 對方不會跑我們的
前端。所以這裡把同一套幾何與配色搬到後端。

刻意輸出 **SVG 而不是 PNG**：PNG 需要額外的繪圖套件（cairo 之類），為了一張由矩形和
文字組成的圖引進一個系統相依不划算；SVG 在 `<img>` 裡一樣能顯示，而且縮放不糊。

⚠️ 這張圖會被貼到別人的頁面上，所有文字都必須跳脫（裝置名稱是使用者輸入）。
"""

from __future__ import annotations

from typing import Any

from app.services.rack import (
    ANGLE_HOLE_PITCH_MM,
    ANGLE_PLY_MM,
    ANGLE_POST_MM,
    KALLAX_DIVIDER_MM,
    RACK_REF_WIDTH_MM,
    RACK_REF_WIDTH_PX,
    RACK_SLOTS,
    board_default_mm,
    brace_levels,
    floor_default_mm,
    has_open_top,
    kallax_columns,
    level_boards_px,
    level_render_px,
    normalize_finish,
    rack_side_px,
    scaled_board_px,
    uses_rack_units,
    v_px_per_mm,
)

#: 與前端 `GEO` 相同的幾何，改這裡要同步改 `rackGraphicsExport.ts`，否則兩邊會長得不一樣
ROW_H = 24
COL_W = 260
GUTTER = 32
PAD = 12
HEADER_H = 30

#: 與前端 `rackTypeColor()` 相同的配色
_COLORS: dict[str, str] = {
    "router": "rgba(99, 102, 241, 0.85)",
    "switch": "rgba(34, 197, 94, 0.85)",
    "firewall": "rgba(239, 68, 68, 0.85)",
    "ap": "rgba(59, 130, 246, 0.85)",
    "server": "rgba(52, 104, 158, 0.9)",
    "storage": "rgba(245, 158, 11, 0.85)",
    "ipmi": "rgba(236, 72, 153, 0.6)",
    "patch_panel": "rgba(20, 184, 166, 0.75)",
    "pdu": "rgba(217, 119, 6, 0.8)",
    "ups": "rgba(202, 138, 4, 0.85)",
    "workstation": "rgba(14, 165, 233, 0.8)",
}
_DEFAULT_COLOR = "rgba(107, 114, 128, 0.7)"
#: 「其他」與沒分類的：灰底加斜紋（跟畫面一樣），伺服器是鋼藍（10-23 客戶：整櫃灰灰的分不出來）
#: 細線、間距寬、對比低（10-24 客戶：2～4U 的大面積密紋看了眼花）
_OTHER_PATTERN = ('<pattern id="rd-other" width="14" height="14" patternUnits="userSpaceOnUse" '
                  'patternTransform="rotate(45)">'
                  f'<rect width="14" height="14" fill="{_DEFAULT_COLOR}"/>'
                  '<rect width="2" height="14" fill="rgba(255,255,255,0.13)"/></pattern>')

#: 各型態各顏色的配色。**與前端 `utils/rackFinish.ts` 的 FINISH_COLORS 相同**
#: （tests/test_rack_more_kinds.py 會比對兩邊），否則畫面與嵌入圖的顏色會不一樣。
#: 角鋼：post＝立柱正面、edge＝另一片翼（側面看過去的那條邊）、beam＝鋼橫桿、hole＝葫蘆孔
#: KALLAX：frame＝外框與隔板、cell＝格子裡面（沒有背板，看到的是更深一點的內側）
#: LACK：wood＝桌面與桌腳
FINISH_COLORS: dict[str, dict[str, dict[str, str]]] = {
    "angle_shelf": {
        "black": {"post": "#303338", "edge": "#1c1e21", "beam": "#35383d",
                  "hole": "rgba(255,255,255,0.32)"},
        "white": {"post": "#eceef0", "edge": "#c3c8cd", "beam": "#e3e6e9",
                  "hole": "rgba(0,0,0,0.38)"},
        "galvanized": {"post": "#b9c0c6", "edge": "#8b939a", "beam": "#aeb5bc",
                       "hole": "rgba(0,0,0,0.42)"},
    },
    "kallax": {
        "white": {"frame": "#f4f4f1", "cell": "#e4e4df", "line": "#c6c6c0"},
        "black_brown": {"frame": "#3d332d", "cell": "#2a221e", "line": "#1f1915"},
        "oak": {"frame": "#e2d3b8", "cell": "#d2c0a0", "line": "#b9a585"},
    },
    "lackrack": {
        # 輪廓線要看得出來：白色桌子放在白色卡片上、黑色桌腳貼著黑色桌面（使用者回報「沒框線」）
        "white": {"wood": "#f4f4f2", "line": "#8f8f89"},
        "black": {"wood": "#2c2c2e", "line": "#66666a"},
        "brown": {"wood": "#5c4736", "line": "#221810"},
    },
}
#: 角鋼層架上面那片夾板的顏色（與前端相同）
PLY_COLOR = "#d6b17c"


def _palette(kind: str, finish: str | None) -> dict[str, str]:
    return FINISH_COLORS[kind][normalize_finish(kind, finish) or ""]


def _esc(v: Any) -> str:
    """SVG 文字跳脫。裝置名稱是使用者輸入，少了這個就是注入點。"""
    return (
        str(v if v is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


# 窄到這個寬度以下就不畫名稱（擠不下，畫了只是一團糊）。改用 <title> 讓瀏覽器顯示
# 原生 tooltip —— 嵌入用的 SVG 沒有我們自己的 hover 層，這是唯一能把名稱留住的辦法。
_MIN_LABEL_W = 46.0


def _part_geom(dev: dict[str, Any], rack_left: float,
               col_w: float) -> tuple[float, float, float]:
    """依橫向格位算出 (x, width, center_x)。與前端 RackDiagram 的 partGeom 同一套幾何。"""
    slot = int(dev.get("rack_slot") or 0)
    span = int(dev.get("rack_slot_span") or RACK_SLOTS)
    cell = col_w / RACK_SLOTS
    x = rack_left + 2 + slot * cell
    w = span * cell - 4
    return x, max(w, 2.0), x + max(w, 2.0) / 2


def build_rack_svg(name: str, u_height: int, devices: list[dict[str, Any]],
                   *, kind: str | None = None, width_mm: int | None = None,
                   row_height_mm: int | None = None,
                   level_heights: list[int] | None = None,
                   board_mm: int | None = None,
                   finish: str | None = None,
                   floor_mm: int | None = None) -> str:
    """單一機櫃的 SVG。`devices` 每筆要有 name / type / u_position / u_size /
    rack_slot / rack_slot_span / rack_face。

    kind/width_mm/row_height_mm/level_heights 給非標準的層架用（issue #30）；
    不給就是標準 19" 機櫃，
    尺寸與改版前完全相同。
    """
    col_w, row_px = level_render_px(kind, width_mm, row_height_mm, level_heights, u_height)
    braces = brace_levels(kind, width_mm, row_height_mm, u_height, level_heights)
    # 層板厚度也要換成 px，否則層板只是畫上去的裝飾、不占高度，總高會比實物矮一截
    board = board_default_mm(kind) if board_mm is None else float(board_mm)
    board_px = scaled_board_px(kind, board_mm, width_mm, row_height_mm, level_heights, u_height)
    bpx = max(board_px, 2.0) if board > 0 else 0.0
    top_px, boards = level_boards_px(kind, u_height, bpx)
    # 離地：只有新型態畫（桌腳、角鋼的腳）；既有型態的嵌入圖本來就不畫，維持原樣
    fl = floor_default_mm(kind) if floor_mm is None else float(floor_mm)
    floor_px = fl * v_px_per_mm(kind) if (fl and kind in _NEW_KINDS) else 0.0
    return _build(name, u_height, devices, kind or "rack", col_w, row_px, braces,
                  bpx, has_open_top(kind), rack_side_px(kind, width_mm),
                  boards=boards, top_px=top_px, floor_px=floor_px, finish=finish,
                  cols=kallax_columns(width_mm) if kind == "kallax" else 1)


_NEW_KINDS = frozenset({"angle_shelf", "kallax", "lackrack"})


# 鍍鉻層架的圓管立柱漸層（與前端 .rack-frame.is-wire 的 CSS 同一組色，兩邊要看起來一樣）
_CHROME = (
    '<linearGradient id="chrome" x1="0" y1="0" x2="1" y2="0">'
    '<stop offset="0%" stop-color="#6f7479"/><stop offset="18%" stop-color="#9aa0a6"/>'
    '<stop offset="38%" stop-color="#eef1f3"/><stop offset="48%" stop-color="#ffffff"/>'
    '<stop offset="62%" stop-color="#d3d8dc"/><stop offset="82%" stop-color="#9aa0a6"/>'
    '<stop offset="100%" stop-color="#63686d"/></linearGradient>'
)

# 立柱整根的細溝槽環：鍍鉻層架最好認的特徵，沒有它只是兩根灰長條（前端用
# repeating-linear-gradient 做同一件事）。pattern 的高度＝溝槽間距。
_GROOVES = (
    '<pattern id="grooves" width="1" height="5" patternUnits="userSpaceOnUse">'
    '<rect x="0" y="0" width="100%" height="1" fill="rgba(70,76,82,0.20)"/></pattern>'
)


# 松木側架的木紋漸層與孔位。IVAR 的側架是一片有整排孔位的松木框 —— 孔位那一排
# 是它最好認的特徵，跟鍍鉻層架的圓管立柱完全是兩回事（前端用同一組色）。
# 側架是一片**平的**松木板，不是圓管：整片幾乎同一個木色，只有右緣一條窄窄的暗邊
# 當厚度，沒有圓柱那種由暗到亮再到暗的漸層。
_PINE = (
    '<linearGradient id="pine" x1="0" y1="0" x2="1" y2="0">'
    '<stop offset="0%" stop-color="#dcb98d"/><stop offset="80%" stop-color="#d4ae7f"/>'
    '<stop offset="100%" stop-color="#b38a58"/></linearGradient>'
)
_PINE_BOARD = (
    '<linearGradient id="pineboard" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0%" stop-color="#e9c99d"/><stop offset="55%" stop-color="#d2a56f"/>'
    '<stop offset="100%" stop-color="#a5763f"/></linearGradient>'
)
# 側架上的調整孔：整排貫穿，是 IVAR 一眼就認得出來的地方。
# 實物規格：孔距（中心至中心）32mm、孔徑 7mm —— 照圖面的比例尺換算成 px，
# 不要用隨手挑的像素值，否則放大看就和層板的比例對不起來。
# pattern 的寬度要等於側架寬度，否則圓會被 1px 的圖磚裁掉（第一版就是這樣什麼都沒畫出來）。
_SIDE_W = 14.0
PEG_PITCH_MM = 32.0
PEG_DIA_MM = 7.0
# 層架寬高同一個比例（見 rack.v_px_per_mm）
_PEG_PITCH = PEG_PITCH_MM * v_px_per_mm("wood_shelf")
_PEG_R = PEG_DIA_MM * v_px_per_mm("wood_shelf") / 2
_PEGS = (
    f'<pattern id="pegs" width="{_SIDE_W}" height="{_PEG_PITCH:.3f}" '
    f'patternUnits="userSpaceOnUse">'
    f'<circle cx="{_SIDE_W / 2}" cy="{_PEG_PITCH / 2:.3f}" r="{_PEG_R:.3f}" '
    f'fill="rgba(74,48,24,0.5)"/>'
    f'<circle cx="{_SIDE_W / 2}" cy="{_PEG_PITCH / 2 - 0.5:.3f}" r="{_PEG_R:.3f}" '
    f'fill="rgba(255,240,215,0.30)"/></pattern>'
)
# 木紋：幾道很淡的直紋，讓側架不會像一塊純色
_GRAIN = (
    '<pattern id="grain" width="7" height="1" patternUnits="userSpaceOnUse">'
    '<rect x="0" y="0" width="1" height="100%" fill="rgba(120,80,40,0.10)"/></pattern>'
)


def _brace(left: float, u: int, levels: int,
           COL_W: float, bounds: list[float]) -> list[str]:
    """木質層架背面的 OBSERVATÖR 支撐桿：**兩根交叉成 X**（官方商品圖就是 X）。

    每根是固定 100 公分的鋼條，所以 X 的垂直跨距由層架寬度決定（見 rack.py 的
    brace_span_mm）—— 寬的跨得矮、窄的跨得高。裝在最底下那一段。
    """
    if levels <= 0:
        return []
    y_bot = bounds[u] - 2
    y_top = bounds[u - levels] + 2
    x0, x1 = left + 4, left + COL_W - 4
    return [
        f'<line x1="{a}" y1="{y_bot}" x2="{b}" y2="{y_top}" '
        f'stroke="#aeb4ba" stroke-width="2.5" stroke-linecap="round" opacity="0.9"/>'
        for a, b in ((x0, x1), (x1, x0))
    ]


def _frame(kind: str, left: float, u: int, COL_W: float,
           bounds: list[float], brace_levels: int = 0,
           board_px: float = 0.0, open_top: bool = False) -> list[str]:
    """依型態畫外框：標準／工業是箱體，層架類不封邊，鍍鉻層架另外畫兩根圓管立柱。

    `bounds` 是每一層的邊界 y（共 u+1 個，由上往下）—— 層架的層高可以一層一層不同，
    所以位置一律查這張表，不能用「第幾層 × 固定列高」去算。
    """
    top, h = bounds[0], bounds[u] - bounds[0]
    bd = max(board_px, 2.0)      # 層板畫多厚（至少 2px，否則細到看不見）
    # 層板的位置。開放頂端時最上面那一列的**上面**沒有板（那裡就是開放的），
    # 所以跳過第一個邊界 —— 不跳的話 5 層會畫出 7 片板。
    boards = bounds[1:] if open_top else bounds
    # 立柱／側架只到**最上面那片層板**為止，不會再往上長 —— 頂板上面是開放的，
    # 東西就放在那裡。所以開放頂端時立柱從 boards[0] 起算，不是從畫面最上緣。
    p_top = boards[0]
    p_h = bounds[u] - p_top
    if kind == "industrial":
        return [f'<rect x="{left}" y="{top}" width="{COL_W}" height="{h}" '
                f'fill="#eceef1" stroke="#5a5f69" stroke-width="4"/>']
    if kind == "wire_shelf":
        post = 11.0

        def _post(x: float) -> list[str]:
            # 圓管 → 溝槽 → 每層的套環，三層疊上去（順序＝前端那三層背景）
            return [
                f'<rect x="{x}" y="{p_top - 6}" width="{post}" height="{p_h + 12}" '
                f'rx="{post / 2}" fill="url(#chrome)" stroke="rgba(0,0,0,.18)"/>',
                f'<rect x="{x}" y="{p_top - 6}" width="{post}" height="{p_h + 12}" '
                f'rx="{post / 2}" fill="url(#grooves)"/>',
                *[f'<rect x="{x}" y="{b - bd}" width="{post}" height="{bd + 2}" '
                  f'fill="url(#chrome)" stroke="rgba(60,66,72,.5)" stroke-width="0.8"/>'
                  for b in boards],
            ]

        return [
            f"<defs>{_CHROME}{_GROOVES}</defs>",
            # 層板（含最上面的頂板：層架頂端幾乎一定有一片，少畫了就像少一層）
            *[f'<rect x="{left}" y="{b - bd}" width="{COL_W}" height="{bd}" '
              f'fill="url(#chrome)"/>' for b in boards],
            # 兩側圓管立柱（畫在層板之上，看起來像穿過去）
            *_post(left - post / 2),
            *_post(left + COL_W - post / 2),
        ]
    if kind == "wood_shelf":
        side = _SIDE_W         # 松木側架比圓管粗，它是一片框不是一根柱
        def _side(x: float) -> list[str]:
            return [
                # 方柱：沒有圓角（IVAR 的側架是方料，不是圓管）
                f'<rect x="{x}" y="{p_top - 8}" width="{side}" height="{p_h + 16}" '
                f'fill="url(#pine)" stroke="rgba(90,60,30,.55)" stroke-width="1"/>',
                f'<rect x="{x}" y="{p_top - 8}" width="{side}" height="{p_h + 16}" '
                f'fill="url(#grain)"/>',
                f'<rect x="{x}" y="{p_top - 8}" width="{side}" height="{p_h + 16}" '
                f'fill="url(#pegs)"/>',
            ]
        return [
            f"<defs>{_PINE}{_PINE_BOARD}{_PEGS}{_GRAIN}</defs>",
            # 背面的 OBSERVATÖR 支撐桿（X）：跨距由寬度決定（見 rack.py）。
            # 畫在層板與裝置的「後面」—— 實物就在背面，空層才看得到它，順序不能反。
            *_brace(left, u, brace_levels, COL_W, bounds),
            # 層板：實心松木，比鍍鉻的橫桿厚。range 從 0 起＝連最上面的頂板一起畫：
            # 層架最上面幾乎一定有一片板當頂，少畫了就會像少一層。
            *[f'<rect x="{left}" y="{b - bd}" width="{COL_W}" height="{bd}" '
              f'fill="url(#pineboard)" stroke="rgba(90,60,30,.35)" stroke-width="0.6"/>'
              for b in boards],
            # 側架整根畫在層板外側（實物就是層板架在兩片側架之間），不像鍍鉻立柱那樣
            # 跨在角落 —— 跨過去會被裝置方塊蓋掉半邊，孔位就看不見了。
            *_side(left - side),
            *_side(left + COL_W),
        ]
    if kind == "shelf":
        return [f'<rect x="{left}" y="{b - bd}" width="{COL_W}" height="{bd}" '
                f'fill="#8b9096"/>' for b in boards]
    return [f'<rect x="{left}" y="{top}" width="{COL_W}" height="{h}" '
            f'fill="#f5f5f5" stroke="#888" stroke-width="1.5"/>']


#: 機櫃的導軌（立柱）寬，與前端匯出的 sideWidth() 相同
_RAIL_W = 8.0


def _channels(left: float, top: float, h: float, COL_W: float, side: float) -> list[str]:
    """櫃體外殼＋兩側走線空間（刻線＝理線槽）＋導軌。

    與前端 `buildRacksSvg` 同一套畫法與配色 —— 這是同一張圖的第三份實作，
    畫面改了這裡沒跟上，嵌到別人頁面上的圖就會跟畫面不一樣。
    """
    sw = _RAIL_W
    x0 = left - sw - side
    out = [f'<rect x="{x0:.2f}" y="{top}" width="{COL_W + 2 * sw + 2 * side:.2f}" height="{h}" '
           f'fill="#f7f8f9" stroke="#888" stroke-width="1.5"/>']
    for cx in (x0, left + COL_W + sw):
        out.append(f'<rect class="rack-channel" x="{cx:.2f}" y="{top}" width="{side:.2f}" '
                   f'height="{h}" fill="#eceef0"/>')
        ty = top + 7
        while ty < top + h:
            out.append(f'<line x1="{cx + 2:.2f}" y1="{ty}" x2="{cx + side - 2:.2f}" y2="{ty}" '
                       f'stroke="#c4c9cf" stroke-width="1.5"/>')
            ty += 14
    for px in (left - sw, left + COL_W):
        out.append(f'<rect x="{px:.2f}" y="{top}" width="{sw}" height="{h}" '
                   f'fill="#c3c8ce" stroke="#8a8f95" stroke-width="0.5"/>')
    return out


def _angle_defs(finish: str | None) -> str:
    """角鋼立柱上的葫蘆孔：一個圓孔接一條往下的窄槽，整排等距（孔距 30mm）。"""
    c = _palette("angle_shelf", finish)
    fid = normalize_finish("angle_shelf", finish)
    w = ANGLE_POST_MM * RACK_REF_WIDTH_PX / RACK_REF_WIDTH_MM
    pitch = ANGLE_HOLE_PITCH_MM * v_px_per_mm("angle_shelf")
    cx = w * 0.56
    return (f'<pattern id="keyholes-{fid}" width="{w:.3f}" height="{pitch:.3f}" '
            f'patternUnits="userSpaceOnUse">'
            f'<circle cx="{cx:.2f}" cy="{pitch * 0.3:.2f}" r="2.3" fill="{c["hole"]}"/>'
            f'<rect x="{cx - 0.8:.2f}" y="{pitch * 0.3:.2f}" width="1.6" height="{pitch * 0.32:.2f}" '
            f'fill="{c["hole"]}"/></pattern>')


def _angle(left: float, COL_W: float, bounds: list[float], boards: list[float],
           rows: int, floor_px: float, finish: str | None) -> list[str]:
    """角鋼層架：兩支 L 型立柱（整排葫蘆孔）＋每層一條鋼橫桿，上面跨放一片夾板。"""
    c = _palette("angle_shelf", finish)
    fid = normalize_finish("angle_shelf", finish)
    w = ANGLE_POST_MM * RACK_REF_WIDTH_PX / RACK_REF_WIDTH_MM
    ply = ANGLE_PLY_MM * v_px_per_mm("angle_shelf")
    out: list[str] = []
    # 橫桿勾在兩支立柱之間、立柱在前面：先畫橫桿（只到立柱內緣），再畫立柱蓋上去
    for i in range(rows):
        b = boards[i]
        if b <= 0:
            continue
        y0 = bounds[i + 1] - b
        pl = min(ply, b * 0.5)
        out += [
            f'<rect x="{left:.2f}" y="{y0 + pl:.2f}" width="{COL_W:.2f}" '
            f'height="{b - pl:.2f}" fill="{c["beam"]}"/>',
            f'<rect class="angle-ply" x="{left:.2f}" y="{y0:.2f}" width="{COL_W:.2f}" '
            f'height="{pl:.2f}" fill="{PLY_COLOR}"/>',
        ]
    # 立柱從最上面那條橫桿的頂端開始（上面是開放的，柱子不會再往上長），往下到地面
    p_top = bounds[1] - boards[0]
    p_bot = bounds[rows] + floor_px
    for x, edge_x in ((left - w, left - w), (left + COL_W, left + COL_W + w - 2)):
        out += [
            f'<rect class="angle-post" x="{x:.2f}" y="{p_top:.2f}" width="{w:.2f}" '
            f'height="{p_bot - p_top:.2f}" fill="{c["post"]}"/>',
            f'<rect x="{x:.2f}" y="{p_top:.2f}" width="{w:.2f}" height="{p_bot - p_top:.2f}" '
            f'fill="url(#keyholes-{fid})"/>',
            # 另一片翼：從正面看是立柱外緣的一條暗邊
            f'<rect x="{edge_x:.2f}" y="{p_top:.2f}" width="2" height="{p_bot - p_top:.2f}" '
            f'fill="{c["edge"]}"/>',
        ]
    return out


def _kallax(left: float, COL_W: float, side: float, bounds: list[float], boards: list[float],
            rows: int, cols: int, finish: str | None) -> tuple[list[str], list[str]]:
    """KALLAX：外框＋一格一格。回 (畫在裝置下面的, 畫在裝置上面的)。

    直的內隔板畫在裝置**上面**：資料上的橫向格位是整片寬度切 60 格，格子之間的隔板
    不占格位，畫在上面才看得出「裝置放在格子裡」。
    """
    c = _palette("kallax", finish)
    div = KALLAX_DIVIDER_MM * RACK_REF_WIDTH_PX / RACK_REF_WIDTH_MM
    box_top = bounds[1] - boards[0]          # 頂部那一列底下就是外框頂板
    box_bot = bounds[rows]
    under = [f'<rect x="{left - side:.2f}" y="{box_top:.2f}" width="{COL_W + 2 * side:.2f}" '
             f'height="{box_bot - box_top:.2f}" fill="{c["frame"]}" stroke="{c["line"]}" '
             f'stroke-width="1"/>']
    cw = (COL_W - (cols - 1) * div) / cols
    for i in range(1, rows):
        y = bounds[i]
        h = bounds[i + 1] - bounds[i] - boards[i]
        for k in range(cols):
            under.append(f'<rect class="kallax-cell" x="{left + k * (cw + div):.2f}" y="{y:.2f}" '
                         f'width="{cw:.2f}" height="{h:.2f}" fill="{c["cell"]}"/>')
    inner_top = box_top + boards[0]
    inner_bot = box_bot - boards[rows - 1]
    over = [f'<rect class="kallax-divider" x="{left + k * (cw + div) - div:.2f}" y="{inner_top:.2f}" '
            f'width="{div:.2f}" height="{inner_bot - inner_top:.2f}" fill="{c["frame"]}"/>'
            for k in range(1, cols)]
    return under, over


def _lack(left: float, COL_W: float, side: float, bounds: list[float], boards: list[float],
          rows: int, floor_px: float, finish: str | None) -> list[str]:
    """LackRack：桌面＋兩支桌腳（正面只看得到兩支），設備鎖在兩腳之間；疊幾張就有幾片桌面。

    最上面一列是桌面上方（可以放東西），它底下那片板＝最上面那張桌子的桌面；
    疊起來的桌子之間那片板是「上面那張的腳下空隙＋下面那張的桌面」，桌面畫在最下面那一截。
    """
    c = _palette("lackrack", finish)
    slab = boards[0]
    y0, y1 = bounds[1] - slab, bounds[rows] + floor_px
    out = [f'<rect class="lack-leg" x="{x:.2f}" y="{y0:.2f}" width="{side:.2f}" '
           f'height="{y1 - y0:.2f}" fill="{c["wood"]}" stroke="{c["line"]}" stroke-width="1"/>'
           for x in (left - side, left + COL_W)]
    slabs = [bounds[i + 1] - slab for i in range(rows) if boards[i] > 0]
    out += [f'<rect class="lack-top" x="{left - side:.2f}" y="{y:.2f}" width="{COL_W + 2 * side:.2f}" '
            f'height="{slab:.2f}" fill="{c["wood"]}" stroke="{c["line"]}" stroke-width="1"/>'
            for y in slabs]
    return out


def _cabinet_panels(kind: str, left: float, COL_W: float, edge: float, top: float,
                    roof: float, bottom: float, base: float) -> list[str]:
    """機櫃的頂板與底座：整個櫃寬（含走線空間與導軌）的實心板，與畫面同色。"""
    fill, line = ("#8a9098", "#5a5f69") if kind == "industrial" else ("#cdd2d8", "#888")
    x0, w = left - edge, COL_W + 2 * edge
    return [
        f'<rect class="rack-roof" x="{x0:.2f}" y="{top - roof:.2f}" width="{w:.2f}" '
        f'height="{roof:.2f}" fill="{fill}" stroke="{line}" stroke-width="1"/>',
        f'<rect class="rack-base" x="{x0:.2f}" y="{bottom - base:.2f}" width="{w:.2f}" '
        f'height="{base:.2f}" fill="{fill}" stroke="{line}" stroke-width="1"/>',
    ]


def _build(name: str, u_height: int, devices: list[dict[str, Any]],
           kind: str, col_w: float, row_px: list[float], brace_levels: int = 0,
           board_px: float = 0.0, open_top: bool = False, side: float = 0.0, *,
           boards: list[float] | None = None, top_px: float = 0.0, floor_px: float = 0.0,
           finish: str | None = None, cols: int = 1) -> str:
    # 尺寸用參數傳，不改模組常數：同一支程序可能同時畫標準機櫃與層架，
    # 暫時覆蓋全域值在任何並行情境下都會畫錯（而且不會報錯，只是尺寸不對）。
    COL_W = col_w
    u = max(int(u_height or 0), 1)
    # 有走線空間時，設備區左邊多了「走線空間＋導軌」；U 數字與標題跟著最外緣走。
    # KALLAX／LackRack 的 side 是外框／桌腳本身，沒有另外的導軌。
    channels = side > 0 and kind in ("rack", "industrial")
    edge = side + _RAIL_W if channels else (side if kind in ("kallax", "lackrack") else 0.0)
    if kind == "angle_shelf":
        edge = side
    rack_left = PAD + GUTTER + edge
    # 0 的時候不要加：int + 0.0 會變成 float，既有型態的座標就從 42 變成 42.0（嵌入圖逐位元組不變）
    top = HEADER_H + PAD + top_px if top_px else HEADER_H + PAD
    # 每一層各自的高度（`row_px` 由第 1 層起算），換成由上往下的邊界表。
    # 層架的層高可以一層一層不同，所以任何位置都查這張表，不能乘固定列高。
    px = list(row_px) if row_px else [ROW_H] * u
    px = (px + [px[-1]] * u)[:u]
    # 層架的最上面那片板**上面**也放得了東西 → 多畫一列（第 u+1 層），高度比照最高那層。
    rows_drawn = u + 1 if open_top else u
    if open_top:
        px = [*px, px[-1]]
    # 每一列底下那片板的厚度（由上往下）。沒給就是每片一樣厚 —— 既有型態都走這條。
    bl = list(boards) if boards is not None else [board_px] * rows_drawn
    bl = (bl + [board_px] * rows_drawn)[:rows_drawn]
    bounds: list[float] = [top]
    for i in range(rows_drawn):
        # 由上往下＝從最高的那一列開始；每一列下面還有一片層板要占高度
        bounds.append(bounds[-1] + px[rows_drawn - 1 - i] + bl[i])
    width = round(PAD * 2 + GUTTER + COL_W + 2 * edge, 2)
    height = HEADER_H + PAD * 2 + (bounds[rows_drawn] - top)
    if top_px or floor_px:
        height = round(height + top_px + floor_px, 2)

    over: list[str] = []
    if kind == "angle_shelf":
        body = _angle(rack_left, COL_W, bounds, bl, rows_drawn, floor_px, finish)
    elif kind == "kallax":
        body, over = _kallax(rack_left, COL_W, side, bounds, bl, rows_drawn, cols, finish)
    elif kind == "lackrack":
        body = _lack(rack_left, COL_W, side, bounds, bl, rows_drawn, floor_px, finish)
    else:
        body = [*(_channels(rack_left, top, bounds[rows_drawn] - top, COL_W, side) if channels else []),
                *_frame(kind, rack_left, rows_drawn, COL_W, bounds, brace_levels, board_px, open_top)]
        if kind in ("rack", "industrial") and (top_px or bl[-1]):
            body += _cabinet_panels(kind, rack_left, COL_W, edge, top, top_px, bounds[rows_drawn],
                                    bl[-1])

    p: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="sans-serif">',
        *([f"<defs>{_angle_defs(finish)}</defs>"] if kind == "angle_shelf" else []),
        *([f"<defs>{_OTHER_PATTERN}</defs>"]
          if any(str(d.get("type") or "") not in _COLORS for d in devices) else []),
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{rack_left - edge:.2f}" y="{PAD + 16}" font-size="14" font-weight="bold">'
        f"{_esc(name)} ({u}{'U' if uses_rack_units(kind) else _esc(' 層')})</text>",
        *body,
    ]
    for i in range(rows_drawn):
        y, y2 = bounds[i], bounds[i + 1]
        lvl = rows_drawn - i
        # 頂板上方那一列沒有層號，標「頂」—— 它不是第 11 層，是第 10 層的板子上面
        label = _esc(" 頂") if (open_top and lvl > u) else str(lvl)
        # 置中在那一列自己的空間（扣掉底下那片板；機櫃最下面那一 U 底下是底座）
        p.append(
            f'<text x="{rack_left - edge - 4:.2f}" y="{(y + y2 - bl[i]) / 2 + 4}" font-size="10" '
            f'text-anchor="end" fill="#666">{label}</text>'
        )
        p.append(
            f'<line x1="{rack_left}" y1="{y}" x2="{rack_left + COL_W}" y2="{y}" '
            f'stroke="#dddddd" stroke-width="0.5"/>'
        )

    geoms: dict[int, tuple[float, float]] = {}
    for idx, dev in enumerate(devices):
        pos, size = dev.get("u_position"), dev.get("u_size")
        if not pos or not size:
            continue                      # 沒有位置的裝置不畫（畫了也不知道畫在哪）
        u_top = int(pos) + int(size) - 1
        # 裝置從第 pos 層佔到第 pos+size-1 層：上緣是最高那層的上邊界，
        # 高度是它跨過那幾層的高度總和（各層高度可能不同，不能乘 size）。
        y_top = bounds[rows_drawn - u_top]
        hgt = sum(px[int(pos) - 1:int(pos) - 1 + int(size)])
        # 層內的垂直位置：層架一層可以疊放、也可以不放滿。
        # 0 貼著層板 ＝ 這一段的**下緣**，所以由下往上長。
        v0 = int(dev.get("rack_vslot") or 0)
        vspan = int(dev.get("rack_vslot_span") or RACK_SLOTS)
        if v0 or vspan < RACK_SLOTS:
            cell = hgt / RACK_SLOTS
            y_bot = y_top + hgt
            hgt = vspan * cell
            y_top = y_bot - (v0 + vspan) * cell
        geoms[idx] = (y_top + 1, hgt - 2)
        x, w, cx = _part_geom(dev, rack_left, COL_W)
        color = _COLORS.get(str(dev.get("type") or ""), "url(#rd-other)")
        p.append(
            f'<rect x="{x}" y="{y_top + 1}" width="{w}" height="{hgt - 2}" '
            f'fill="{color}" stroke="rgba(0,0,0,0.3)"/>'
        )
        # 名稱永遠進 <title>（滑過看得到）；太窄就不畫文字，避免糊成一團
        p.append(f'<title>{_esc(dev.get("name"))}</title>')
        if w >= _MIN_LABEL_W:
            # 一律置中（含整列寬）：網頁版的立面圖就是置中，兩邊不一致看起來像 bug。
            # 靠左是舊的 full / left / right 模型留下來的，那時整列寬是唯一的「非一半」。
            p.append(
                f'<text x="{cx}" y="{y_top + hgt / 2 + 4}" text-anchor="middle" '
                f'font-size="11" font-weight="bold" fill="#ffffff">{_esc(dev.get("name"))}</text>'
            )
        if dev.get("rack_face") == "rear":
            rx = x + w
            p.append(
                f'<path d="M{rx - 14} {y_top + 1} L{rx} {y_top + 1} L{rx} {y_top + 15} Z" '
                f'fill="rgba(0,0,0,0.55)"/>'
            )
            p.append(
                f'<text x="{rx - 2}" y="{y_top + 11}" text-anchor="end" font-size="9" '
                f'font-weight="bold" fill="#ffffff">R</text>'
            )
    p.extend(over)
    marks = _group_marks(devices, geoms)
    if marks:
        # 有群組才加寬（沒有群組的嵌入圖逐位元組不變）：右側多一條括線軌道＋標籤
        lanes = max(m["lane"] for m in marks) + 1
        extra = lanes * _GROUP_LANE_PX + _GROUP_LABEL_PX
        new_w = round(width + extra, 2)
        p[0] = p[0].replace(f'width="{width}"', f'width="{new_w}"', 1).replace(
            f'viewBox="0 0 {width} {height}"', f'viewBox="0 0 {new_w} {height}"', 1)
        bg = f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>'
        p = [f'<rect x="0" y="0" width="{new_w}" height="{height}" fill="#ffffff"/>' if x == bg else x for x in p]
        rail_x = rack_left + COL_W + edge + 6
        for m in marks:
            bx = rail_x + m["lane"] * _GROUP_LANE_PX
            y0, h = m["y"], m["h"]
            ticks = "".join(f'<line x1="{bx}" y1="{y0 + t}" x2="{bx + 6}" y2="{y0 + t}" stroke="{m["color"]}" '
                            f'stroke-width="2"/>' for t in m["ticks"])
            lx = rail_x + lanes * _GROUP_LANE_PX + 4
            lw = min(_GROUP_LABEL_PX - 8, len(m["label"]) * 6.4 + 12)
            ly = y0 + h / 2
            p.append(f'<g class="rack-group"><path d="M{bx} {y0} H{bx + 6} V{y0 + h} H{bx}" fill="none" '
                     f'stroke="{m["color"]}" stroke-width="2"/>{ticks}'
                     f'<rect x="{lx}" y="{ly - 9}" width="{lw}" height="18" rx="4" fill="{m["color"]}" '
                     f'fill-opacity="0.15" stroke="{m["color"]}"/>'
                     f'<text x="{lx + 6}" y="{ly + 4}" font-size="11" font-weight="bold" fill="#1f2937">'
                     f'{_esc(m["label"])}</text></g>')
    p.append("</svg>")
    return "\n".join(p)


# ── 裝置群組的括線（與前端 utils/rackGroups.ts、rackGraphicsExport.ts 同一套規則）─────────
_GROUP_LANE_PX = 10
_GROUP_LABEL_PX = 130
_GROUP_PALETTE = ("#0f9d8a", "#d9822b", "#c2185b", "#7b5cd6", "#2e7d32", "#b8860b", "#00838f", "#ad1457")


def _group_color(gid: str) -> str:
    h = 0
    for ch in gid:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return _GROUP_PALETTE[h % len(_GROUP_PALETTE)]


def _group_marks(devices: list[dict[str, Any]], geoms: dict[int, tuple[float, float]]) -> list[dict[str, Any]]:
    """devices 的 group = {id, name, total}；geoms 是裝置（依 devices 的索引）畫出來的 (y_top, 高度)。"""
    by: dict[str, dict[str, Any]] = {}
    for i, dev in enumerate(devices):
        g = dev.get("group")
        if not g or i not in geoms:
            continue
        e = by.setdefault(str(g["id"]), {"g": g, "boxes": []})
        e["boxes"].append(geoms[i])
    marks: list[dict[str, Any]] = []
    for gid, e in by.items():
        y = min(b[0] for b in e["boxes"])
        bottom = max(b[0] + b[1] for b in e["boxes"])
        name = str(e["g"].get("name") or "")
        name = f"{name[:15]}…" if len(name) > 16 else name
        total = int(e["g"].get("total") or len(e["boxes"]))
        marks.append({"id": gid, "color": _group_color(gid), "label": f"{name} {len(e['boxes'])}/{total}",
                      "y": y, "h": max(10.0, bottom - y), "lane": 0,
                      "ticks": [b[0] + b[1] / 2 - y for b in e["boxes"]]})
    # 軌道：由上往下排，跟已經放的任何一條重疊就往外挪一條
    marks.sort(key=lambda m: (m["y"], m["label"]))
    placed: list[dict[str, Any]] = []
    for m in marks:
        lane = 0
        while any(q["lane"] == lane and q["y"] <= m["y"] + m["h"] and m["y"] <= q["y"] + q["h"] for q in placed):
            lane += 1
        m["lane"] = lane
        placed.append(m)
    return marks
