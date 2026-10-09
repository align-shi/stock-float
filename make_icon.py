# -*- coding: utf-8 -*-
"""生成浮窗快捷方式的图标：深色圆角底板 + 红色上升折线 + 双色柱。

超采样 4 倍绘制（先画 1024 再交给 Pillow 压成各档尺寸），保证 16/32px 下
边缘不毛糙。配色沿用浮窗深色主题，红涨绿跌。
"""
import io
import math
import os

from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.abspath(__file__))
S = 1024

BG = (27, 30, 36, 255)        # #1b1e24 与浮窗深色背景一致
BORDER = (58, 65, 80, 255)    # #3a4150
UP = (255, 90, 92, 255)       # #ff5a5c 红涨
DOWN = (47, 208, 122, 255)    # #2fd07a 绿跌
GRID = (52, 58, 70, 255)

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)


def rrect(box, radius, **kw):
    d.rounded_rectangle(box, radius=radius, **kw)


# 圆角底板
rrect([0, 0, S - 1, S - 1], int(S * 0.22), fill=BG,
      outline=BORDER, width=int(S * 0.013))

# 两条参考横线（很淡，暗示坐标系）
for fy in (0.40, 0.62):
    y = int(S * fy)
    d.line([(int(S * 0.14), y), (int(S * 0.86), y)], fill=GRID, width=int(S * 0.012))

# 底部两根柱子：左绿（跌）右红（涨）
bw = int(S * 0.125)
base = int(S * 0.82)
rrect([int(S * 0.17), base - int(S * 0.20), int(S * 0.17) + bw, base],
      bw // 3, fill=DOWN)
rrect([int(S * 0.70), base - int(S * 0.36), int(S * 0.70) + bw, base],
      bw // 3, fill=UP)

# 上升折线（末端留出箭头的空间）
pts = [(int(S * 0.17), int(S * 0.58)), (int(S * 0.36), int(S * 0.46)),
       (int(S * 0.51), int(S * 0.53)), (int(S * 0.70), int(S * 0.34))]
LW = int(S * 0.072)
d.line(pts, fill=UP, width=LW, joint="curve")

# 箭头：以末点方向为轴，尖端沿轴前伸，两后角各偏 150°
p2, p3 = pts[-2], pts[-1]
vx, vy = p3[0] - p2[0], p3[1] - p2[1]
L = math.hypot(vx, vy)
ux, uy = vx / L, vy / L
ah = int(S * 0.135)                     # 箭头大小：底边宽约 2× 线宽
tip = (p3[0] + ux * ah * 0.62, p3[1] + uy * ah * 0.62)
corners = []
for deg in (150.0, -150.0):
    rad = math.radians(deg)
    rx = ux * math.cos(rad) - uy * math.sin(rad)
    ry = ux * math.sin(rad) + uy * math.cos(rad)
    corners.append((tip[0] + rx * ah, tip[1] + ry * ah))
d.polygon([tip] + corners, fill=UP)

# 起点圆头
r0 = int(S * 0.036)
d.ellipse([pts[0][0] - r0, pts[0][1] - r0, pts[0][0] + r0, pts[0][1] + r0], fill=UP)

ICO = os.path.join(BASE, "stock-widget.ico")
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
img.save(ICO, format="ICO", sizes=SIZES)

# 预览：各档尺寸放大拼一张图，方便肉眼确认小尺寸下是否糊
PREVIEW = os.path.join(BASE, "icon-preview.png")
shots = []
for w, h in [(16, 16), (32, 32), (48, 48), (256, 256)]:
    im = img.resize((w, h), Image.LANCZOS)
    scale = (256 // w) or 1
    shots.append(im.resize((w * scale * 2, h * scale * 2), Image.NEAREST))

pad = 24
W = sum(s.width for s in shots) + pad * (len(shots) + 1)
H = max(s.height for s in shots) + pad * 2
canvas = Image.new("RGBA", (W, H), (245, 245, 247, 255))
x = pad
for s in shots:
    canvas.alpha_composite(s, (x, pad + (H - pad * 2 - s.height) // 2))
    x += s.width + pad
canvas.save(PREVIEW)

out = io.StringIO()
out.write("图标已生成: %s  (%d B)\n" % (ICO, os.path.getsize(ICO)))
out.write("放大预览: %s\n" % PREVIEW)

# 读回来确认各档都真的写进 ico 了
with Image.open(ICO) as im:
    got = sorted(im.ico.sizes()) if hasattr(im, "ico") else []
out.write("ico 内含尺寸: %s\n" % (got,))

open(os.path.join(BASE, "icon.log"), "w", encoding="utf-8").write(out.getvalue())
