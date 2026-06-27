#!/usr/bin/env python3
"""採用アイコンを PIL で高解像度描画し、各サイズへ書き出す。
- LEFT  = 配分ドーナツ＋中央ビル＋緑直線矢印  → iPhoneホーム画面(apple-touch)/PWA
- RIGHT = 緑棒グラフ＋金直線矢印             → PCタブfavicon / ページ左上
比率は承認済みウィジェット（tile=240基準）と同一。
出力: assets/icons/
"""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "icons"
SS = 4  # スーパーサンプリング

BLUE = (59, 130, 246); GREEN = (34, 197, 94); AMBER = (245, 158, 11); RED = (239, 68, 68)
NAVY = (30, 58, 138); WHITE = (255, 255, 255); BORDER = (226, 232, 240)
GOLD = (245, 158, 11)
BARS = [(134, 239, 172), (74, 222, 128), (34, 197, 94), (21, 128, 61)]


def _line(d, p0, p1, color, w):
    d.line([p0, p1], fill=color, width=w)
    r = w / 2
    for (x, y) in (p0,):  # 始点に丸キャップ
        d.ellipse([x - r, y - r, x + r, y + r], fill=color)


def draw_left(S):
    """ドーナツ＋ビル＋緑直線矢印。RGBA、全面白（不透明）。"""
    img = Image.new("RGBA", (S, S), WHITE + (255,))
    d = ImageDraw.Draw(img)
    cx = cy = 0.5 * S
    R = 0.325 * S
    inner = 0.20 * S
    bbox = [cx - R, cy - R, cx + R, cy + R]
    start = -90.0
    for col, deg in ((BLUE, 144), (GREEN, 108), (AMBER, 72), (RED, 36)):
        d.pieslice(bbox, start, start + deg, fill=col)
        start += deg
    d.ellipse([cx - inner, cy - inner, cx + inner, cy + inner], fill=WHITE)
    # ビル2棟
    for (rx, ry, rw, rh) in ((0.408, 0.500, 0.075, 0.167), (0.500, 0.433, 0.075, 0.233)):
        d.rounded_rectangle([rx * S, ry * S, (rx + rw) * S, (ry + rh) * S],
                            radius=0.01 * S, fill=NAVY)
    # 緑直線矢印
    _line(d, (0.375 * S, 0.625 * S), (0.583 * S, 0.450 * S), GREEN, int(0.026 * S))
    d.polygon([(0.629 * S, 0.413 * S), (0.600 * S, 0.471 * S), (0.563 * S, 0.429 * S)], fill=GREEN)
    return img


def draw_right(S):
    """緑棒グラフ＋金直線矢印。RGBA、全面白。"""
    img = Image.new("RGBA", (S, S), WHITE + (255,))
    d = ImageDraw.Draw(img)
    bars = [(0.125, 0.650, 0.108, 0.183), (0.275, 0.525, 0.108, 0.308),
            (0.425, 0.400, 0.108, 0.433), (0.575, 0.275, 0.108, 0.558)]
    for (rx, ry, rw, rh), col in zip(bars, BARS):
        d.rounded_rectangle([rx * S, ry * S, (rx + rw) * S, (ry + rh) * S],
                            radius=0.012 * S, fill=col)
    _line(d, (0.10 * S, 0.70 * S), (0.658 * S, 0.279 * S), GOLD, int(0.026 * S))
    d.polygon([(0.713 * S, 0.238 * S), (0.679 * S, 0.304 * S), (0.638 * S, 0.254 * S)], fill=GOLD)
    return img


def render(fn, target, rounded=False, opaque=True, border=False):
    S = target * SS
    img = fn(S)
    if border:
        ImageDraw.Draw(img).rounded_rectangle(
            [1, 1, S - 2, S - 2], radius=0.22 * S if rounded else 0.0,
            outline=BORDER, width=max(2, int(0.006 * S)))
    if rounded:
        mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=0.22 * S, fill=255)
        img.putalpha(mask)
    img = img.resize((target, target), Image.LANCZOS)
    if opaque and not rounded:
        bg = Image.new("RGB", (target, target), WHITE)
        bg.paste(img.convert("RGBA"), (0, 0), img.convert("RGBA"))
        img = bg
    return img


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # LEFT: iPhoneホーム画面 / PWA（全面白・角丸なし＝iOSが自動マスク）
    render(draw_left, 180, rounded=False, opaque=True).save(OUT / "apple-touch-icon.png")
    render(draw_left, 192, rounded=False, opaque=True).save(OUT / "icon-192.png")
    render(draw_left, 512, rounded=False, opaque=True).save(OUT / "icon-512.png")
    # RIGHT: favicon / ページ左上（角丸・透過）
    for sz in (16, 32, 48, 64):
        render(draw_right, sz, rounded=True).save(OUT / f"favicon-{sz}.png")
    render(draw_right, 128, rounded=True).save(OUT / "header-icon.png")
    render(draw_right, 256, rounded=True).save(OUT / "icon-right-512.png")
    # favicon.ico（複数サイズ内包）
    ico = render(draw_right, 256, rounded=True)
    ico.save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print("wrote:", *(p.name for p in sorted(OUT.iterdir())))


if __name__ == "__main__":
    main()
