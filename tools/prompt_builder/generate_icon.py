"""
generate_icon.py
Рисует иконку приложения программно (без внешних изображений) —
скруглённый квадрат в цвет тёмно-фиолетового заголовка окна с простым
геометричным глифом из трёх слоёв-"блоков" (отсылка к блочному
редактору). Сохраняет assets/app_icon.png (256x256, для iconphoto)
и assets/app_icon.ico (мультиразмерный, для заголовка/панели задач Windows).

Запускать один раз при сборке: python assets/generate_icon.py
"""
from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw

OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# Палитра — та же "тёмно-фиолетовая" гамма, что и у цветного заголовка окна.
BG_TOP = (138, 47, 114)      # #8a2f72
BG_BOTTOM = (58, 18, 56)     # #3a1238
BLOCK_1 = (255, 255, 255, 235)
BLOCK_2 = (230, 190, 225, 210)
BLOCK_3 = (200, 140, 200, 190)
BORDER = (40, 12, 38, 255)


def _rounded_square_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # Вертикальный градиент фона.
    grad = Image.new("RGB", (1, size), (0, 0, 0))
    for y in range(size):
        t = y / max(1, size - 1)
        r = int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
        g = int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
        b = int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
        grad.putpixel((0, y), (r, g, b))
    grad = grad.resize((size, size))

    radius = int(size * 0.22)
    mask = _rounded_square_mask(size, radius)
    img.paste(grad, (0, 0), mask)

    draw = ImageDraw.Draw(img, "RGBA")
    draw.rounded_rectangle([1, 1, size - 2, size - 2], radius=radius, outline=BORDER, width=max(1, size // 64))

    # Три смещённых скруглённых "блока" — символ блочного редактора.
    block_size = size * 0.40
    offset = size * 0.13
    cx, cy = size * 0.50, size * 0.56

    def block(cx_off, cy_off, scale, fill):
        w = block_size * scale
        x0 = cx + cx_off - w / 2
        y0 = cy + cy_off - w / 2
        x1 = x0 + w
        y1 = y0 + w
        r = w * 0.28
        draw.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=fill)

    block(-offset, -offset, 1.0, BLOCK_3)
    block(offset * 0.15, -offset * 0.15, 1.0, BLOCK_2)
    block(offset * 1.05, offset * 1.05, 1.0, BLOCK_1)

    return img


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    base = draw_icon(256)
    base.save(os.path.join(OUT_DIR, "app_icon.png"))

    icons = [draw_icon(s) for s in sizes]
    icons[-1].save(
        os.path.join(OUT_DIR, "app_icon.ico"),
        sizes=[(s, s) for s in sizes],
    )
    print("Иконки сохранены:", os.path.join(OUT_DIR, "app_icon.png"), "и app_icon.ico")


if __name__ == "__main__":
    main()
