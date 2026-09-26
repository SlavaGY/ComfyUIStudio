"""
generate_icons.py
==================
Единый генератор всех иконок ComfyUI Studio из одного файла-первоисточника
`assets/branding/app_icon_master.png` (квадрат, см. соседний
app_icon_master.png — неоновая атомная спираль ДНК на чёрном фоне).

Запускать вручную из корня репозитория после смены арта:

    python assets/branding/generate_icons.py

Что генерирует:
  - Квадратные .png/.ico (16..256 px) для трёх десктоп-приложений —
    Studio (assets/), Prompt Builder (comfyui_studio/prompt_builder/assets/),
    PromptVault (comfyui_studio/promptvault/resources/). Все три ссылаются
    на эти файлы напрямую (QIcon/.iconbitmap/иконка .exe в *.spec), рисовать
    их программно (как раньше делал prompt_builder/generate_icon.py) больше
    не нужно.
  - Android adaptive icon (API 26+, единственный формат, который реально
    используется — minSdk приложения и так 26, см. app/build.gradle.kts):
    background — сплошной цвет из угла мастер-картинки (она сама на чёрном
    фоне), foreground — тот же рисунок, уменьшенный и отцентрованный так,
    чтобы уместиться в безопасную зону (~66% диаметра) адаптивной иконки и
    не обрезаться круглой/квадратной/капле-видной маской лаунчера.
  - Классические (до-Oreo) ic_launcher.png/ic_launcher_round.png по
    плотностям экрана — на самом деле никогда не показываются (minSdk 26
    гарантирует, что всегда используется adaptive-icon), генерируются
    только для полноты набора ресурсов/на случай будущего снижения minSdk.

Не трогает: assets/icon.ico использует другой производственный путь
(PyInstaller `icon=` в *.spec) -- он просто читает готовый .ico с диска,
пересобирать .exe не нужно, чтобы файл обновился в уже собранной версии
далее.
"""

from __future__ import annotations

import os

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MASTER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_icon_master.png")

ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
SQUARE_PNG_SIZE = 256

# Приложение → (папка, имя файла без расширения)
DESKTOP_TARGETS = [
    ("assets", "icon"),
    (os.path.join("comfyui_studio", "prompt_builder", "assets"), "app_icon"),
    (os.path.join("comfyui_studio", "promptvault", "resources"), "icon"),
]

ANDROID_RES = os.path.join("android", "app", "src", "main", "res")
# density -> сторона ic_launcher.png/ic_launcher_round.png в px
ANDROID_DENSITIES = {
    "mdpi": 48,
    "hdpi": 72,
    "xhdpi": 96,
    "xxhdpi": 144,
    "xxxhdpi": 192,
}
ADAPTIVE_FOREGROUND_SIZE = 432
# Официальная безопасная зона адаптивных иконок — круг ~66% диаметра
# канвы: гарантированно не обрезается никакой маской лаунчера (круглой,
# квадратной, «капля» и т. п.).
ADAPTIVE_SAFE_ZONE_FRACTION = 0.66
# Доля стороны foreground-канвы, которую занимает сам рисунок после
# прозрачной обводки по яркости (см. _alpha_key_black) — при этом
# масштабе самая дальняя от центра яркая точка укладывается в безопасную
# зону с запасом (проверяется автоматически в _adaptive_foreground).
ADAPTIVE_ART_SCALE = 0.60


def _load_master() -> Image.Image:
    im = Image.open(MASTER_PATH).convert("RGBA")
    if im.width != im.height:
        raise ValueError(f"Мастер-иконка не квадратная: {im.size}")
    return im


def _corner_color(im: Image.Image) -> tuple[int, int, int]:
    """Цвет фона мастер-картинки (сэмплируется из угла) — используется как
    сплошной background адаптивной иконки, чтобы всё бесшовно совпадало."""
    r, g, b, _a = im.getpixel((0, 0))
    return (r, g, b)


def _resized(im: Image.Image, size: int) -> Image.Image:
    return im.resize((size, size), Image.LANCZOS)


def _alpha_key_black(im: Image.Image) -> Image.Image:
    """Прозрачность по яркости: чистый чёрный (фон мастер-картинки) —
    alpha=0, дальше линейно растёт с max(R,G,B) — неоновое свечение
    затухает естественно, а не обрывается жёсткой маской. Нужно только
    для adaptive-иконки: без этого весь квадрат (включая чёрные поля)
    считался бы непрозрачным рисунком, которому надо помещаться в
    безопасную зону, — хотя чёрный на чёрном фоне и так невидим при
    любой обрезке."""
    arr = np.asarray(im.convert("RGB"), dtype=np.uint16)
    alpha = arr.max(axis=2).astype(np.uint8)
    rgba = np.dstack([arr.astype(np.uint8), alpha])
    return Image.fromarray(rgba, "RGBA")


def write_desktop_icons(master: Image.Image) -> None:
    base = _resized(master, SQUARE_PNG_SIZE)
    ico_frames = [_resized(master, s) for s in ICO_SIZES]
    for folder, stem in DESKTOP_TARGETS:
        out_dir = os.path.join(ROOT, folder)
        os.makedirs(out_dir, exist_ok=True)
        png_path = os.path.join(out_dir, f"{stem}.png")
        ico_path = os.path.join(out_dir, f"{stem}.ico")
        base.save(png_path)
        # PIL требует, чтобы кадры были отдельными изображениями точных
        # размеров — sizes= на изображении меньшем, чем нужный размер,
        # апскейлит с потерей резкости, поэтому кадры готовим сами.
        ico_frames[-1].save(ico_path, sizes=[(s, s) for s in ICO_SIZES])
        print(f"  {png_path}")
        print(f"  {ico_path}")


def _adaptive_foreground(master: Image.Image) -> Image.Image:
    """432x432, RGBA: рисунок с прозрачным (по яркости, см.
    _alpha_key_black) чёрным фоном, уменьшенный до ADAPTIVE_ART_SCALE и
    отцентрованный на прозрачном поле. Настоящая прозрачность (не залитые
    чёрным поля) важна и по смыслу — при параллаксе на лаунчерах, которые
    его поддерживают, foreground и background слои двигаются независимо,
    и залитые вручную поля дали бы видимый шов на границе рисунка при
    таком сдвиге, — и практически: она же убирает из проверки безопасной
    зоны угол картинки (сплошной чёрный), оставляя только настоящее
    содержимое (спираль, атомы, зелёные перемычки)."""
    keyed = _alpha_key_black(master)
    art_size = round(ADAPTIVE_FOREGROUND_SIZE * ADAPTIVE_ART_SCALE)
    art = _resized(keyed, art_size)
    canvas = Image.new("RGBA", (ADAPTIVE_FOREGROUND_SIZE, ADAPTIVE_FOREGROUND_SIZE), (0, 0, 0, 0))
    offset = (ADAPTIVE_FOREGROUND_SIZE - art_size) // 2
    canvas.alpha_composite(art, (offset, offset))
    _assert_within_safe_zone(canvas)
    return canvas


def _assert_within_safe_zone(foreground: Image.Image, alpha_threshold: int = 8) -> None:
    """Проверка на этапе генерации, а не только вручную после: если кто-то
    в будущем увеличит ADAPTIVE_ART_SCALE или сменит мастер-картинку на
    менее компактную композицию, скрипт откажется молча выпустить иконку,
    которую обрежет круглая маска лаунчера."""
    size = foreground.size[0]
    alpha = np.asarray(foreground)[:, :, 3]
    yy, xx = np.mgrid[0:size, 0:size]
    center = (size - 1) / 2
    dist = np.sqrt((yy - center) ** 2 + (xx - center) ** 2)
    safe_radius = size * ADAPTIVE_SAFE_ZONE_FRACTION / 2
    opaque = alpha > alpha_threshold
    if not opaque.any():
        return
    max_radius = dist[opaque].max()
    if max_radius > safe_radius:
        raise RuntimeError(
            f"Рисунок выходит за безопасную зону адаптивной иконки: "
            f"{max_radius:.1f}px от центра при допустимых {safe_radius:.1f}px "
            f"(ADAPTIVE_ART_SCALE={ADAPTIVE_ART_SCALE}). Уменьшите ADAPTIVE_ART_SCALE."
        )


def write_android_icons(master: Image.Image) -> None:
    bg_color = _corner_color(master)
    foreground = _adaptive_foreground(master)
    # "Плоская" версия (без прозрачности) для классических до-Oreo
    # ресурсов — сама сцена собирается так же, как её соберёт система из
    # adaptive-icon XML (background цвет под прозрачным foreground).
    flattened = Image.new("RGB", foreground.size, bg_color)
    flattened.paste(foreground, (0, 0), foreground)

    fg_dir = os.path.join(ROOT, ANDROID_RES, "mipmap-xxxhdpi")
    os.makedirs(fg_dir, exist_ok=True)
    fg_path = os.path.join(fg_dir, "ic_launcher_foreground.png")
    foreground.save(fg_path)
    print(f"  {fg_path}")

    bg_xml_path = os.path.join(ROOT, ANDROID_RES, "values", "ic_launcher_background.xml")
    hex_color = "#{:02X}{:02X}{:02X}".format(*bg_color)
    with open(bg_xml_path, "r", encoding="utf-8") as f:
        xml = f.read()
    import re
    new_xml, n = re.subn(
        r'(<color name="ic_launcher_background">)#[0-9A-Fa-f]{6}(</color>)',
        rf"\g<1>{hex_color}\g<2>",
        xml,
    )
    # Сверяем число замен, а не new_xml == xml: если новый цвет совпал со
    # старым (например, оба #000000 — так и было при повторном запуске без
    # смены арта), строки итак совпадут, и это неотличимо от "паттерн не
    # найден"; n показывает совпадения regex напрямую.
    if n != 1:
        raise RuntimeError(f"Не удалось найти строку с цветом в {bg_xml_path}")
    with open(bg_xml_path, "w", encoding="utf-8") as f:
        f.write(new_xml)
    print(f"  {bg_xml_path} -> {hex_color}")

    for density, size in ANDROID_DENSITIES.items():
        d = os.path.join(ROOT, ANDROID_RES, f"mipmap-{density}")
        os.makedirs(d, exist_ok=True)
        square = _resized(flattened, size)
        square_path = os.path.join(d, "ic_launcher.png")
        round_path = os.path.join(d, "ic_launcher_round.png")
        # ic_launcher.png/round.png — мёртвый ресурс при minSdk 26 (см.
        # докстринг модуля), поэтому оба файла делаем одинаковыми плоскими
        # квадратами вместо ручной обрезки в круг: система их не читает.
        square.save(square_path)
        square.save(round_path)
        print(f"  {square_path}")
        print(f"  {round_path}")


def main() -> None:
    master = _load_master()
    print("Десктопные иконки:")
    write_desktop_icons(master)
    print("Android иконки:")
    write_android_icons(master)


if __name__ == "__main__":
    main()
