"""
Обёртка над ultimate_rvc для приложения Studio -- ЕДИНСТВЕННОЕ место, где
этот пакет импортируется.

ultimate_rvc тяжёлый (torch/CUDA, десятки модулей), поэтому импорт идёт в
фоновом потоке (start_warmup): веб-сервер поднимается сразу, а интерфейс
показывает «загрузка движка…», пока Engine.state == "loading". Ошибка
импорта (нет CUDA-сборки, сломанное окружение) не роняет сервер -- она
остаётся в Engine.error и показывается в интерфейсе.

Опора на публичные функции модулей core (сигнатуры взяты из исходников):
  ultimate_rvc.core.generate.song_cover.run_pipeline(...)
  ultimate_rvc.core.generate.speech.run_pipeline(...), get_edge_tts_voice_names()
  ultimate_rvc.core.manage.models: get_voice_model_names, download_voice_model,
      upload_voice_model, delete_voice_models, load_public_models_table,
      get_public_model_tags
Перечисления F0Method/EmbedderModel/AudioExt -- из ultimate_rvc.typing_extra;
их члены НЕ зашиты здесь, а читаются у настоящего класса (options()).

Для тестов загрузчик подменяется (loader=): он возвращает объект с теми же
атрибутами, что default_loader().
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from types import SimpleNamespace

from . import params as P

log = logging.getLogger("studio_rvc.engine")

LOADING = "loading"
READY = "ready"
ERROR = "error"

SONG_RESULT_LABELS = (
    "Готовый кавер",
    "Исходная песня",
    "Вокал",
    "Инструментал",
    "Основной вокал",
    "Бэк-вокал",
    "Основной вокал без реверберации",
    "Реверберация",
    "Преобразованный вокал",
    "Вокал с эффектами",
    "Инструментал (сдвиг тона)",
    "Бэк-вокал (сдвиг тона)",
)


def default_loader():
    """Импортирует ultimate_rvc и собирает нужное в одно пространство имён."""
    from ultimate_rvc.core.generate import song_cover, speech
    from ultimate_rvc.core.manage import models
    from ultimate_rvc.typing_extra import AudioExt, EmbedderModel, F0Method

    return SimpleNamespace(
        song_cover=song_cover,
        speech=speech,
        models=models,
        F0Method=F0Method,
        EmbedderModel=EmbedderModel,
        AudioExt=AudioExt,
    )


def enum_options(enum_cls):
    """[{value, label}] по реальным членам перечисления."""
    return [{"value": str(m.value), "label": m.name} for m in enum_cls]


def to_enum(enum_cls, raw):
    """Строка из интерфейса -> член перечисления: по значению или по имени
    (без учёта регистра). Неизвестное -- ParamError со списком допустимых."""
    text = str(raw).strip().lower()
    for member in enum_cls:
        if str(member.value).lower() == text or member.name.lower() == text:
            return member
    allowed = ", ".join(str(m.value) for m in enum_cls)
    raise P.ParamError(f"Недопустимое значение «{raw}». Допустимо: {allowed}")


class _Progress:
    """Подставляется вместо gradio.Progress: display_progress() ultimate_rvc
    вызывает progress_bar(доля, desc=сообщение) на каждом этапе. Через него
    идут и прогресс, и отмена (report() бросает JobCancelled)."""

    def __init__(self, report):
        self._report = report

    def __call__(self, progress=None, desc=None, total=None, unit="steps", **_kwargs):
        if isinstance(progress, (tuple, list)) and len(progress) == 2 and progress[1]:
            progress = progress[0] / progress[1]
        elif not isinstance(progress, (int, float)):
            progress = None
        self._report(progress, _clean_stage(desc))

    def tqdm(self, iterable=None, desc=None, total=None, unit="steps", **_kwargs):
        self._report(None, _clean_stage(desc))
        return iterable


def _clean_stage(text):
    text = str(text or "").strip()
    return text[3:].strip() if text.startswith("[~]") else text


class _StageLogHandler(logging.Handler):
    """Запасной канал этапов: display_progress() кроме progress_bar пишет в
    лог сообщения вида «[~] Separating vocals...». Если вдруг прогресс-бар
    не вызывается, этап всё равно будет виден (без процента и без отмены)."""

    def __init__(self, report):
        super().__init__(level=logging.INFO)
        self._report = report

    def emit(self, record):
        message = record.getMessage()
        if message.startswith("[~]"):
            self._report(None, _clean_stage(message), check_cancel=False)


class Engine:
    def __init__(self, data_dir, loader=default_loader):
        self.data_dir = Path(data_dir)
        self._loader = loader
        self._mods = None
        self.state = LOADING
        self.error = ""
        self._warmup = None
        self._edge_voices = None
        self._lock = threading.Lock()

    # -- загрузка ----------------------------------------------------------------

    def start_warmup(self):
        if self._warmup is None:
            self._warmup = threading.Thread(target=self._load, name="rvc-warmup", daemon=True)
            self._warmup.start()

    def _load(self):
        try:
            self._mods = self._loader()
        except ImportError as e:
            # Типичная причина: приложение запущено не питоном venv RVC.
            self.error = f"Не удалось импортировать ultimate_rvc: {e}"
            self.state = ERROR
            log.exception("Ошибка импорта ultimate_rvc")
        except OSError as e:
            self.error = f"Не удалось загрузить ultimate_rvc: {e}"
            self.state = ERROR
            log.exception("Ошибка загрузки ultimate_rvc")
        else:
            self.state = READY
            log.info("ultimate_rvc загружен")

    def mods(self):
        if self._mods is None:
            raise RuntimeError(self.error or "Движок RVC ещё загружается")
        return self._mods

    # -- справочники ------------------------------------------------------------------

    def options(self):
        m = self.mods()
        return {
            "f0_methods": enum_options(m.F0Method),
            "embedder_models": enum_options(m.EmbedderModel),
            "audio_formats": enum_options(m.AudioExt),
            "f0_default": str(m.F0Method.RMVPE.value),
            "embedder_default": str(m.EmbedderModel.CONTENTVEC.value),
            "format_default": str(m.AudioExt.MP3.value),
            "sample_rates": list(P.OUTPUT_SAMPLE_RATES),
        }

    def voice_models(self):
        return list(self.mods().models.get_voice_model_names())

    def edge_voices(self):
        with self._lock:
            if self._edge_voices is None:
                # Список тянется из сети (Edge TTS) -- кэшируем на время работы.
                self._edge_voices = list(self.mods().speech.get_edge_tts_voice_names())
            return list(self._edge_voices)

    def public_models(self):
        m = self.mods().models
        rows = m.load_public_models_table([])
        return {
            "tags": list(m.get_public_model_tags()),
            "models": [
                {
                    "name": r[0],
                    "description": r[1],
                    "tags": list(r[2]) if isinstance(r[2], (list, tuple)) else r[2],
                    "credit": r[3],
                    "added": str(r[4]),
                    "url": r[5],
                }
                for r in rows
            ],
        }

    # -- управление моделями ----------------------------------------------------------

    def download_model(self, url, name):
        self.mods().models.download_voice_model(url, P.safe_model_name(name))

    def import_model(self, name, paths):
        self.mods().models.upload_voice_model([str(p) for p in paths], P.safe_model_name(name))

    def delete_model(self, name):
        self.mods().models.delete_voice_models([name])

    # -- задания -----------------------------------------------------------------------

    def _pipeline_kwargs(self, raw, kwargs):
        m = self.mods()
        if "f0_method" in kwargs:
            kwargs["f0_method"] = to_enum(m.F0Method, kwargs["f0_method"])
        if "embedder_model" in kwargs:
            kwargs["embedder_model"] = to_enum(m.EmbedderModel, kwargs["embedder_model"])
        if "output_format" in kwargs:
            kwargs["output_format"] = to_enum(m.AudioExt, kwargs["output_format"])
        return kwargs

    def _run(self, fn, report, labels, **kwargs):
        sink = _Progress(report)
        handler = _StageLogHandler(report)
        root = logging.getLogger("ultimate_rvc")
        root.addHandler(handler)
        try:
            paths = fn(progress_bar=sink, **kwargs)
        finally:
            root.removeHandler(handler)
        return [
            {"label": labels[i] if i < len(labels) else "Файл", "path": str(p), "name": Path(p).name}
            for i, p in enumerate(paths)
        ]

    def run_song_cover(self, params, report):
        """params: source, model_name + поля SONG_COVER_SPEC/ENUM_FIELDS."""
        kwargs = self._pipeline_kwargs(params, P.song_cover_kwargs(params))
        return self._run(
            self.mods().song_cover.run_pipeline,
            report,
            SONG_RESULT_LABELS,
            source=params["source"],
            model_name=params["model_name"],
            **kwargs,
        )

    def run_speech(self, params, report):
        """params: source (путь к текстовому файлу), model_name + поля SPEECH_SPEC."""
        kwargs = self._pipeline_kwargs(params, P.speech_kwargs(params))
        return self._run(
            self.mods().speech.run_pipeline,
            report,
            ("Озвучка",),
            source=params["source"],
            model_name=params["model_name"],
            **kwargs,
        )
