"""
shared_promptgen.py
===================
Настройки «Генератора промптов» (кнопка под полем промпта в Imagine,
см. comfyui_studio/imagine/backend/promptgen.py) — общие для лаунчера и
Imagine.

Устроено по образцу shared_theme.py / shared_language.py: один JSON-файл
%APPDATA%\\ComfyUIStudio\\prompt_generator.json, который ПИШЕТ страница
«Генератор промптов» в настройках Studio (ui/settings/
prompt_generator_page.py) и ЧИТАЕТ бэкенд Imagine — при каждом запросе,
а не один раз при старте, поэтому менять пути/префикс можно, не
перезапуская Imagine.

Источник истины — именно этот файл, а не config.json лаунчера: Imagine
живёт отдельным процессом и config.json не читает, а «Сброс настроек»
(Дополнительно) не должен молча стирать пути к моделям.

Модуль сознательно не зависит ни от Qt, ни от FastAPI — им пользуются и
Qt-страница (для живой проверки путей), и веб-бэкенд, и юнит-тесты.
"""

from __future__ import annotations

import glob
import json
import os
import tempfile

SHARED_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "ComfyUIStudio"
)
SHARED_PROMPTGEN_PATH = os.path.join(SHARED_DIR, "prompt_generator.json")

# Логи генератора: promptgen.log (ход каждой задачи с таймингами и
# снимками VRAM/RAM, ротация) и llama-server/<время>-<id>.log (полный вывод
# самого llama-server для каждого запуска, хранятся последние
# LLAMA_LOGS_KEEP штук). Лежат в общей папке комплекта.
LOG_DIR = os.path.join(SHARED_DIR, "logs")
LOG_FILE_NAME = "promptgen.log"
LLAMA_LOGS_KEEP = 15


def log_file_path() -> str:
    return os.path.join(LOG_DIR, LOG_FILE_NAME)


def llama_log_dir() -> str:
    return os.path.join(LOG_DIR, "llama-server")

# Место, куда в префикс подставляется то, что пользователь написал в окне
# генератора. Необязательно: если в префиксе нет этой метки, текст
# пользователя просто дописывается ПОСЛЕ префикса (см. compose_prompt).
INPUT_PLACEHOLDER = "{input}"

# Запрос, когда пользователь прикрепил картинку, но ничего не написал.
IMAGE_ONLY_TEXT = "Describe the attached image."

# Префикс по умолчанию. `{input}` стоит в кавычках, как в исходной
# формулировке (User: "текст пользователя").
DEFAULT_PREFIX = (
    "Write a single medium-length paragraph that visually describes an "
    "image based on the input/instruction:\n\n"
    "Use natural, vivid language. This will be used by a text-to-image "
    "model, so avoid meta phrases like \"The image shows…\" or \"In this "
    "scene…\". Be clear and to the point—avoid euphemisms. If details are "
    "missing, use intuition to expand without straying off-topic. If the "
    "context involves two different genders, include tags like (1male, "
    "1female); if it's a solo subject, no tags. If the input suggests "
    "explicit content, use language appropriate for adult themes. Output "
    "only the image description—no extra comments or messages.\n\n"
    "User: \"" + INPUT_PLACEHOLDER + "\""
)

# Контекст должен вмещать префикс (~300 токенов), картинку (сотни-тысячи
# токенов) и ответ. Типичный ответ -- один абзац, до 8192 (max_tokens) он
# почти не доходит, а KV-кэш растёт линейно с контекстом и отнимает VRAM --
# лишняя память заставляет llama-server (--fit) выгружать часть слоёв на CPU.
DEFAULT_CTX_SIZE = 8192
# Значение по умолчанию первой версии -- см. миграцию в _coerce().
_LEGACY_DEFAULT_CTX_SIZE = 16384
SETTINGS_VERSION = 2

# Выгружать ли модели ComfyUI из VRAM перед запуском llama-server:
#   auto   -- только если свободной видеопамяти не хватает (по оценке)
#   always -- всегда
#   never  -- никогда
FREE_COMFY_MODES = ("auto", "always", "never")

# Максимальная сторона прикреплённой картинки (px): картинку уменьшают в
# браузере до отправки -- число токенов зрения растёт с площадью.
DEFAULT_IMAGE_MAX_SIDE = 1024
IMAGE_MAX_SIDE_RANGE = (256, 4096)

DEFAULTS = {
    # версия схемы файла (см. _coerce -- миграции значений по умолчанию)
    "settings_version": SETTINGS_VERSION,
    # папка с llama-server(.exe) и его DLL
    "llama_dir": "",
    # GGUF-файл модели
    "model_path": "",
    # необязательно: файл mmproj -- включает работу с изображениями
    "mmproj_path": "",
    # текст ПЕРЕД тем, что напишет пользователь (см. compose_prompt)
    "prefix": DEFAULT_PREFIX,
    # -- дополнительно --
    # размер контекста (-c). Должен вмещать префикс + картинку + ответ
    # (до 8192 токенов), поэтому по умолчанию заметно больше 8192.
    "ctx_size": DEFAULT_CTX_SIZE,
    # -ngl: пусто = не передавать (llama-server сам подберёт под VRAM,
    # --fit), либо число / "all" / "auto"
    "gpu_layers": "",
    # ещё одна папка с DLL, добавляемая в PATH процесса llama-server
    # (см. dll_dirs() -- папку LM Studio vendor/ ищем сами)
    "extra_dll_dir": "",
    # произвольные дополнительные аргументы командной строки llama-server
    "extra_args": "",
    # см. FREE_COMFY_MODES
    "free_comfy_mode": "auto",
    # см. DEFAULT_IMAGE_MAX_SIDE
    "image_max_side": DEFAULT_IMAGE_MAX_SIDE,
}


# ---------------------------------------------------------------------------
# Чтение / запись
# ---------------------------------------------------------------------------

def _coerce(data: dict) -> dict:
    """Накладывает прочитанное из файла на DEFAULTS, отбрасывая
    неизвестные ключи и значения неверного типа (повреждённый/
    отредактированный вручную файл не должен ронять ни лаунчер, ни
    Imagine)."""
    cfg = dict(DEFAULTS)
    if not isinstance(data, dict):
        return cfg
    for key, default in DEFAULTS.items():
        if key not in data:
            continue
        value = data[key]
        if isinstance(default, bool):
            if isinstance(value, bool):
                cfg[key] = value
        elif isinstance(default, int):
            try:
                cfg[key] = int(value)
            except (TypeError, ValueError):
                pass
        elif isinstance(default, str):
            if isinstance(value, str):
                cfg[key] = value
            elif value is not None and not isinstance(value, (dict, list)):
                cfg[key] = str(value)
    if cfg["ctx_size"] < 0:
        cfg["ctx_size"] = DEFAULT_CTX_SIZE
    # Файл первой версии (без settings_version) хранил ctx_size=16384 -- это
    # было значением по умолчанию, а не выбором пользователя (страница
    # сохраняет все поля разом). Слишком большой контекст съедает VRAM
    # KV-кэшем и заставляет llama-server оставить часть слоёв на CPU, поэтому
    # такое значение один раз заменяется новым умолчанием. Явно выбранные
    # позже 16384 сохраняются уже с версией 2 и не трогаются.
    if data.get("settings_version", 1) < SETTINGS_VERSION and cfg["ctx_size"] == _LEGACY_DEFAULT_CTX_SIZE:
        cfg["ctx_size"] = DEFAULT_CTX_SIZE
    cfg["settings_version"] = SETTINGS_VERSION
    # первая версия хранила булев `free_comfy_first`: True -> always, а
    # False был просто значением по умолчанию -- он теперь "auto"
    if "free_comfy_mode" not in data and data.get("free_comfy_first") is True:
        cfg["free_comfy_mode"] = "always"
    if cfg["free_comfy_mode"] not in FREE_COMFY_MODES:
        cfg["free_comfy_mode"] = DEFAULTS["free_comfy_mode"]
    lo, hi = IMAGE_MAX_SIDE_RANGE
    if not lo <= cfg["image_max_side"] <= hi:
        cfg["image_max_side"] = DEFAULT_IMAGE_MAX_SIDE
    return cfg


def read_settings() -> dict:
    """Всегда возвращает полный набор ключей (DEFAULTS + то, что лежит в
    файле); отсутствующий/повреждённый файл — просто DEFAULTS."""
    try:
        with open(SHARED_PROMPTGEN_PATH, "r", encoding="utf-8") as f:
            return _coerce(json.load(f))
    except Exception:
        return dict(DEFAULTS)


def write_settings(settings: dict) -> bool:
    """Атомарная запись (временный файл + replace), чтобы Imagine, читающий
    файл в этот же момент, не увидел наполовину записанный JSON. Возвращает
    False при ошибке записи -- вызывающему решать, показывать ли её."""
    # то, что записываем, всегда текущей версии -- иначе _coerce принял бы
    # свежесохранённое значение за «старое» и применил к нему миграцию
    cfg = _coerce({**settings, "settings_version": SETTINGS_VERSION})
    try:
        os.makedirs(SHARED_DIR, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix="prompt_generator.", suffix=".tmp", dir=SHARED_DIR
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, SHARED_PROMPTGEN_PATH)
        except Exception:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Проверка путей
# ---------------------------------------------------------------------------

def find_server_exe(llama_dir: str) -> str | None:
    """Путь к llama-server внутри папки llama.cpp (llama-server.exe на
    Windows; вариант без .exe -- на случай запуска не под Windows)."""
    if not llama_dir:
        return None
    for name in ("llama-server.exe", "llama-server"):
        path = os.path.join(llama_dir, name)
        if os.path.isfile(path):
            return path
    return None


def is_configured(cfg: dict) -> bool:
    """Заданы ли обязательные поля (папка llama.cpp и модель) -- по этому
    признаку Imagine решает, показывать ли кнопку генератора. Сами пути
    здесь НЕ проверяются на существование (см. validate)."""
    return bool(cfg.get("llama_dir", "").strip() and cfg.get("model_path", "").strip())


# Тексты проблем -- отдельным словарём (код -> шаблон с `{}` под путь), а
# не зашитыми в validate(): страница настроек Studio переводит их на
# язык интерфейса через свой словарь переводов (по тем же шаблонам-
# ключам, см. i18n.py), а бэкенд Imagine отдаёт русский оригинал.
MESSAGES = {
    "no_dir": "Не указана папка llama.cpp (Настройки Studio → Генератор промптов).",
    "dir_missing": "Папка llama.cpp не найдена: {}",
    "no_exe": "В папке llama.cpp нет llama-server.exe: {}",
    "no_model": "Не указан файл модели GGUF (Настройки Studio → Генератор промптов).",
    "model_missing": "Файл модели не найден: {}",
    "mmproj_missing": "Файл mmproj не найден: {}",
    "dll_missing": "Дополнительная папка с DLL не найдена: {}",
}


def check(cfg: dict) -> tuple[str, str] | None:
    """None, если всё готово к запуску; иначе (код проблемы, путь/деталь) --
    первая найденная проблема. Коды -- ключи MESSAGES."""
    llama_dir = cfg.get("llama_dir", "").strip()
    model_path = cfg.get("model_path", "").strip()
    mmproj_path = cfg.get("mmproj_path", "").strip()
    extra_dll = cfg.get("extra_dll_dir", "").strip()

    if not llama_dir:
        return ("no_dir", "")
    if not os.path.isdir(llama_dir):
        return ("dir_missing", llama_dir)
    if find_server_exe(llama_dir) is None:
        return ("no_exe", llama_dir)
    if not model_path:
        return ("no_model", "")
    if not os.path.isfile(model_path):
        return ("model_missing", model_path)
    if mmproj_path and not os.path.isfile(mmproj_path):
        return ("mmproj_missing", mmproj_path)
    if extra_dll and not os.path.isdir(extra_dll):
        return ("dll_missing", extra_dll)
    return None


def validate(cfg: dict) -> str | None:
    """То же, что check(), но готовым русским сообщением (или None)."""
    problem = check(cfg)
    if problem is None:
        return None
    code, detail = problem
    return MESSAGES[code].format(detail)


def dll_dirs(cfg: dict) -> list[str]:
    """Папки, которые нужно поставить в начало PATH процесса llama-server.

    LM Studio хранит CUDA-библиотеки (cudart64_12.dll, cublas64_12.dll…)
    НЕ рядом с llama-server.exe, а в соседней папке
    `backends/vendor/<что-то>` и подмешивает её в PATH сама при своём
    запуске. Без этого llama-server.exe из LM Studio молча падает с кодом
    0xC0000135 (не найдена DLL) и даже `--help` ничего не печатает. Поэтому:
      1) сама папка llama.cpp (на случай DLL рядом);
      2) явно указанная пользователем «Доп. папка с DLL»;
      3) автоматически найденные `<родитель папки llama.cpp>/vendor/*`
         (та самая раскладка LM Studio), если в них есть .dll.
    Для обычного релиза llama.cpp с github достаточно пункта 1 (cudart
    лежит в том же архиве/папке).
    """
    llama_dir = cfg.get("llama_dir", "").strip()
    dirs: list[str] = []

    def add(path):
        if path and os.path.isdir(path) and path not in dirs:
            dirs.append(path)

    add(llama_dir)
    add(cfg.get("extra_dll_dir", "").strip())
    if llama_dir:
        parent = os.path.dirname(os.path.abspath(llama_dir))
        for vendor in sorted(glob.glob(os.path.join(parent, "vendor", "*"))):
            if os.path.isdir(vendor) and glob.glob(os.path.join(vendor, "*.dll")):
                add(vendor)
    return dirs


# ---------------------------------------------------------------------------
# Сборка запроса
# ---------------------------------------------------------------------------

def compose_prompt(prefix: str, user_text: str, has_image: bool = False) -> str:
    """Итоговый текст запроса к модели.

    Префикс -- это «просто текст до того, что пишет пользователь», а не
    системный промпт: он и текст пользователя идут ОДНИМ сообщением роли
    user. Если в префиксе есть метка {input}, текст пользователя
    подставляется на её место (так в умолчании он оказывается в кавычках
    после «User:»); иначе дописывается сразу после префикса.

    Метка заменяется обычной подстановкой строки, а не str.format() --
    в префиксе могут быть любые фигурные скобки (например, JSON-примеры).
    """
    text = user_text.strip()
    if not text and has_image:
        text = IMAGE_ONLY_TEXT
    if INPUT_PLACEHOLDER in prefix:
        return prefix.replace(INPUT_PLACEHOLDER, text)
    if prefix and not prefix[-1].isspace():
        prefix += "\n"
    return prefix + text
