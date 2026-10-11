"""
Разбор и проверка параметров заданий (кавер / озвучка) -- только stdlib.

Приложение работает в venv Ultimate RVC и не должно зависеть ни от Qt, ни
от остального кода Studio, поэтому здесь нет ничего, кроме стандартной
библиотеки. Перечисления ultimate_rvc (F0Method, EmbedderModel, AudioExt)
здесь НЕ перечисляются: их члены известны только самому ultimate_rvc, а
угадывать их значения нельзя -- поля enum остаются строками, а в нужный
тип их переводит engine.py, где настоящие классы уже импортированы.
"""

from __future__ import annotations

OUTPUT_SAMPLE_RATES = (16000, 22050, 32000, 40000, 44100, 48000)

# имя -> (тип, минимум, максимум, значение по умолчанию как у run_pipeline)
_COMMON_RVC = {
    "n_octaves": (int, -3, 3, 0),
    "n_semitones": (int, -12, 12, 0),
    "index_rate": (float, 0.0, 1.0, 0.3),
    "rms_mix_rate": (float, 0.0, 1.0, 1.0),
    "protect_rate": (float, 0.0, 0.5, 0.33),
    "autotune_strength": (float, 0.0, 1.0, 1.0),
    "proposed_pitch_threshold": (float, 50.0, 500.0, 155.0),
    "clean_strength": (float, 0.0, 1.0, 0.7),
    "sid": (int, 0, 1000, 0),
    "output_sr": (int, 8000, 96000, 44100),
}
_COMMON_BOOL = {"proposed_pitch": False}

SONG_COVER_SPEC = {
    **_COMMON_RVC,
    "room_size": (float, 0.0, 1.0, 0.15),
    "wet_level": (float, 0.0, 1.0, 0.2),
    "dry_level": (float, 0.0, 1.0, 0.8),
    "damping": (float, 0.0, 1.0, 0.7),
    "main_gain": (int, -20, 20, 0),
    "inst_gain": (int, -20, 20, 0),
    "backup_gain": (int, -20, 20, 0),
}
SONG_COVER_BOOL = {
    **_COMMON_BOOL, "split_vocals": False, "autotune_vocals": False, "clean_vocals": False,
}

SPEECH_SPEC = {
    **_COMMON_RVC,
    "tts_pitch_shift": (int, -100, 100, 0),
    "tts_speed_change": (int, -50, 100, 0),
    "tts_volume_change": (int, -50, 100, 0),
    "output_gain": (int, -20, 20, 0),
}
SPEECH_BOOL = {
    **_COMMON_BOOL, "split_speech": False, "autotune_speech": False, "clean_speech": False,
}

# Поля-перечисления ultimate_rvc (значение -- строка, тип подставит engine).
ENUM_FIELDS = ("f0_method", "embedder_model", "output_format")
STRING_FIELDS = ("custom_embedder_model", "output_name", "tts_voice")


class ParamError(ValueError):
    """Человекочитаемая ошибка параметров -- уходит пользователю как есть."""


def _to_bool(name, value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ("true", "1", "false", "0"):
        return value.strip().lower() in ("true", "1")
    raise ParamError(f"Параметр «{name}» должен быть «да» или «нет»")


def _to_number(name, value, kind, lo, hi):
    if isinstance(value, bool):
        raise ParamError(f"Параметр «{name}» должен быть числом")
    try:
        number = kind(value)
        if kind is int and isinstance(value, float) and value != number:
            raise ValueError
    except (TypeError, ValueError):
        raise ParamError(f"Параметр «{name}» должен быть числом") from None
    if not lo <= number <= hi:
        raise ParamError(f"Параметр «{name}» вне допустимого диапазона {lo}…{hi}")
    return number


def _coerce(raw, spec, bools, allowed_strings):
    kwargs = {}
    for name, (kind, lo, hi, _default) in spec.items():
        if name in raw and raw[name] not in (None, ""):
            kwargs[name] = _to_number(name, raw[name], kind, lo, hi)
    for name in bools:
        if name in raw and raw[name] is not None:
            kwargs[name] = _to_bool(name, raw[name])
    for name in ENUM_FIELDS:
        value = raw.get(name)
        if value not in (None, ""):
            kwargs[name] = str(value)
    for name in allowed_strings:
        value = raw.get(name)
        if value not in (None, ""):
            kwargs[name] = str(value).strip()
    return kwargs


def song_cover_kwargs(raw):
    """Параметры run_pipeline() для каверa (без source/model_name/прогресса)."""
    return _coerce(raw, SONG_COVER_SPEC, SONG_COVER_BOOL, ("custom_embedder_model", "output_name"))


def speech_kwargs(raw):
    """Параметры run_pipeline() для озвучки (без source/model_name/прогресса)."""
    return _coerce(
        raw, SPEECH_SPEC, SPEECH_BOOL, ("custom_embedder_model", "output_name", "tts_voice")
    )


_UNSAFE = '<>:"/\\|?*\x00'


def safe_filename(name, default="file"):
    """Имя файла без путей и запрещённых в Windows символов (имя приходит из
    браузера и попадёт на диск)."""
    name = str(name or "").replace("\\", "/").split("/")[-1]
    name = "".join("_" if (c in _UNSAFE or ord(c) < 32) else c for c in name).strip(" .")
    return name[:120] or default


def safe_model_name(name):
    """Имя голосовой модели -> имя папки. Пустое имя -- ParamError."""
    cleaned = safe_filename(name, default="")
    if not cleaned:
        raise ParamError("Укажите имя модели")
    return cleaned


def is_url(text):
    text = str(text or "").strip().lower()
    return text.startswith("http://") or text.startswith("https://")
