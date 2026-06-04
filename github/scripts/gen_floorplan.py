#!/usr/bin/env python3
"""產生一張像樣的機房平面圖（俯視）：~6 坪、4.0m × 5.0m、含一個出入門。
輸出 PNG 供 jt-ipam 機房平面圖上傳用。"""
from PIL import Image, ImageDraw, ImageFont

PPM = 200                      # px per metre
WALL = int(0.18 * PPM)         # 牆厚 0.18 m
IW, IH = int(4.0 * PPM), int(5.0 * PPM)   # 室內 4.0 × 5.0 m
ML, MR, MT, MB = 170, 110, 220, 190       # 邊距（放尺寸/標題）

OX, OY = ML, MT                                   # 外牆左上
OW, OH = IW + 2 * WALL, IH + 2 * WALL             # 外牆尺寸
IX, IY = OX + WALL, OY + WALL                     # 室內左上
W, H = OX + OW + MR, OY + OH + MB

BG = (255, 255, 255)
WALLC = (58, 63, 75)
FLOOR = (245, 246, 248)
TILE = (224, 228, 234)
DIM = (107, 114, 128)
DOORC = (37, 99, 235)
INK = (17, 24, 39)
SUB = (90, 98, 112)

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

CJK = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
def f(sz):
    return ImageFont.truetype(CJK, sz, index=0)
ft_title, ft_sub, ft_lbl, ft_dim, ft_door = f(48), f(26), f(28), f(26), f(26)

def ctext(xy, s, font, fill, anchor="mm"):
    d.text(xy, s, font=font, fill=fill, anchor=anchor)

# ── 牆體（外實心 → 內挖空成地板）──
d.rectangle([OX, OY, OX + OW, OY + OH], fill=WALLC)
d.rectangle([IX, IY, IX + IW, IY + IH], fill=FLOOR)

# ── 地板架高地磚格線（600mm）──
step = int(0.6 * PPM)
x = IX + step
while x < IX + IW:
    d.line([x, IY, x, IY + IH], fill=TILE, width=1); x += step
y = IY + step
while y < IY + IH:
    d.line([IX, y, IX + IW, y], fill=TILE, width=1); y += step

# ── 出入門（底牆，0.9m 開口；門板 + 開門弧線）──
dw = int(0.9 * PPM)
op_l = IX + int(0.7 * PPM)          # 開口左緣（離左牆 0.7m）
op_r = op_l + dw
by = OY + OH - WALL                  # 底牆內緣 y
# 在底牆挖開口
d.rectangle([op_l, by, op_r, by + WALL], fill=BG)
d.rectangle([op_l, by, op_r, by + WALL], outline=BG)
# 門框兩側小短線
d.line([op_l, by, op_l, by + WALL], fill=WALLC, width=3)
d.line([op_r, by, op_r, by + WALL], fill=WALLC, width=3)
# 鉸鏈在右側（op_r），門板開進室內、指向上方
hinge_x, hinge_y = op_r, by
d.line([hinge_x, hinge_y, hinge_x, hinge_y - dw], fill=DOORC, width=5)        # 門板
d.arc([hinge_x - dw, hinge_y - dw, hinge_x + dw, hinge_y + dw], 180, 270,
      fill=DOORC, width=3)                                                    # 開門弧
ctext(((op_l + op_r) // 2, by + WALL + 26), "門 (出入口)", ft_door, DOORC)

# ── 尺寸標註 ──
def dim_h(x1, x2, y, label):
    d.line([x1, y, x2, y], fill=DIM, width=2)
    for xx in (x1, x2):
        d.line([xx, y - 7, xx, y + 7], fill=DIM, width=2)
    ctext(((x1 + x2) // 2, y - 18), label, ft_dim, DIM)
def dim_v(y1, y2, x, label):
    d.line([x, y1, x, y2], fill=DIM, width=2)
    for yy in (y1, y2):
        d.line([x - 7, yy, x + 7, yy], fill=DIM, width=2)
    img2 = Image.new("RGBA", (120, 40), (0, 0, 0, 0))
    dd = ImageDraw.Draw(img2)
    dd.text((60, 20), label, font=ft_dim, fill=DIM, anchor="mm")
    img.paste(img2.rotate(90, expand=True), (x - 56, (y1 + y2) // 2 - 60), img2.rotate(90, expand=True))

dim_h(IX, IX + IW, OY - 40, "4.0 m")
dim_v(IY, IY + IH, OX - 40, "5.0 m")

# ── 標題 / 面積 ──
ctext((OX, 60), "機房平面圖", ft_title, INK, anchor="lm")
ctext((OX, 108), "≈ 6 坪 ｜ 4.0 m × 5.0 m ｜ 19.8 m²", ft_sub, SUB, anchor="lm")

# ── 指北針（右上）──
nx, ny = OX + OW - 36, OY + 60
d.line([nx, ny + 34, nx, ny - 34], fill=INK, width=3)
d.polygon([(nx, ny - 44), (nx - 9, ny - 24), (nx + 9, ny - 24)], fill=INK)
ctext((nx, ny - 58), "N", ft_dim, INK)

# ── 比例尺（右下，1 m）──
sx, sy = OX + OW - PPM - 20, OY + OH + 70
d.line([sx, sy, sx + PPM, sy], fill=INK, width=3)
for xx in (sx, sx + PPM):
    d.line([xx, sy - 6, xx, sy + 6], fill=INK, width=3)
ctext((sx + PPM // 2, sy + 20), "1 m", ft_dim, INK)

out = "/opt/jt-ipam/machine-room-6ping.png"
img.save(out, "PNG")
print("saved", out, img.size)
