"""Чистые модули приложения RVC: params, jobs, engine (с подменённым загрузчиком).
FastAPI-слой (main.py) здесь не запускается -- fastapi может не быть в окружении
тестов; его проверяют на машине с venv Ultimate RVC (см. docs/history/RVC_APPLY.md)."""

import enum
import threading
import time

import pytest

from comfyui_studio.rvc.backend import engine as E
from comfyui_studio.rvc.backend import jobs as J
from comfyui_studio.rvc.backend import params as P


def _wait(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# --------------------------------------------------------------------------
# params


def test_song_cover_kwargs_coerces_and_skips_empty():
    kw = P.song_cover_kwargs({
        "n_semitones": "3", "index_rate": "0.5", "split_vocals": "true",
        "f0_method": "rmvpe", "output_name": "  my cover ", "room_size": "",
        "unknown": 1,
    })
    assert kw == {"n_semitones": 3, "index_rate": 0.5, "split_vocals": True,
                  "f0_method": "rmvpe", "output_name": "my cover"}


def test_params_defaults_are_not_injected():
    # Значения по умолчанию остаются за run_pipeline -- не дублируем их здесь.
    assert P.song_cover_kwargs({}) == {}
    assert P.speech_kwargs({}) == {}


@pytest.mark.parametrize("raw", [
    {"index_rate": 1.5}, {"n_semitones": 40}, {"n_semitones": "abc"},
    {"n_semitones": 1.5}, {"split_vocals": "maybe"}, {"main_gain": True},
])
def test_song_cover_kwargs_rejects_bad_values(raw):
    with pytest.raises(P.ParamError):
        P.song_cover_kwargs(raw)


def test_speech_kwargs_has_tts_fields_and_no_reverb():
    kw = P.speech_kwargs({"tts_voice": "ru-RU-DmitryNeural", "tts_speed_change": 10,
                          "output_gain": -3, "room_size": 0.5})
    assert kw == {"tts_voice": "ru-RU-DmitryNeural", "tts_speed_change": 10, "output_gain": -3}


def test_safe_filename_strips_paths_and_forbidden_chars():
    assert P.safe_filename("..\\..\\evil/песня:1?.mp3") == "песня_1_.mp3"
    assert P.safe_filename("") == "file"
    assert P.safe_filename("  ..  ") == "file"
    assert len(P.safe_filename("a" * 500)) == 120


def test_safe_model_name_requires_a_name():
    assert P.safe_model_name("My Voice") == "My Voice"
    with pytest.raises(P.ParamError):
        P.safe_model_name("  ")


def test_is_url():
    assert P.is_url(" https://youtu.be/x") and P.is_url("HTTP://a")
    assert not P.is_url("C:\\music\\a.mp3") and not P.is_url(None)


# --------------------------------------------------------------------------
# jobs


def _blocker(gate):
    def runner(job, report):
        gate.wait(5)
        return []
    return runner


def test_job_runs_and_reports_progress():
    def runner(job, report):
        report(0.5, "половина")
        return [{"label": "Готово", "path": "/tmp/x.mp3", "name": "x.mp3"}]

    jm = J.JobManager(runner)
    job = jm.submit("song_cover", "t", {"model_name": "m"})
    assert _wait(lambda: job.status == J.DONE)
    assert job.progress == 1.0 and job.stage == "половина"
    snap = job.snapshot()
    assert snap["files"] == [{"index": 0, "label": "Готово", "name": "x.mp3"}]
    assert "path" not in str(snap)  # пути с диска наружу не уходят
    jm.shutdown()


def test_jobs_run_one_at_a_time_in_order():
    order, gate = [], threading.Event()

    def runner(job, report):
        order.append(("start", job.id))
        if job.id == 1:
            gate.wait(5)
        order.append(("end", job.id))
        return []

    jm = J.JobManager(runner)
    a = jm.submit("k", "a", {})
    b = jm.submit("k", "b", {})
    assert _wait(lambda: a.status == J.RUNNING)
    assert b.status == J.QUEUED
    gate.set()
    assert _wait(lambda: b.status == J.DONE)
    assert order == [("start", 1), ("end", 1), ("start", 2), ("end", 2)]
    jm.shutdown()


def test_cancel_queued_job_is_immediate():
    gate = threading.Event()
    jm = J.JobManager(_blocker(gate))
    first = jm.submit("k", "a", {})
    second = jm.submit("k", "b", {})
    assert _wait(lambda: first.status == J.RUNNING)
    assert jm.cancel(second.id)
    assert second.status == J.CANCELLED
    gate.set()
    assert _wait(lambda: first.status == J.DONE)
    assert second.status == J.CANCELLED  # в работу уже не попадёт
    jm.shutdown()


def test_cancel_running_job_stops_at_next_report():
    started, proceed = threading.Event(), threading.Event()

    def runner(job, report):
        started.set()
        proceed.wait(5)
        report(0.3, "следующий этап")  # здесь и срабатывает отмена
        return [{"label": "не должно быть", "path": "x"}]

    jm = J.JobManager(runner)
    job = jm.submit("k", "a", {})
    assert started.wait(5)
    assert jm.cancel(job.id) and job.cancel_requested
    proceed.set()
    assert _wait(lambda: job.status == J.CANCELLED)
    assert job.files == []
    jm.shutdown()


def test_cancel_after_pipeline_already_finished_marks_cancelled():
    started, proceed = threading.Event(), threading.Event()

    def runner(job, report):
        started.set()
        proceed.wait(5)
        return [{"label": "x", "path": "x"}]  # report() больше не вызывается

    jm = J.JobManager(runner)
    job = jm.submit("k", "a", {})
    assert started.wait(5)
    jm.cancel(job.id)
    proceed.set()
    assert _wait(lambda: job.status in J.FINISHED)
    assert job.status == J.CANCELLED
    jm.shutdown()


def test_cancel_unknown_or_finished_returns_false():
    jm = J.JobManager(lambda job, report: [])
    job = jm.submit("k", "a", {})
    assert _wait(lambda: job.status == J.DONE)
    assert not jm.cancel(job.id) and not jm.cancel(999)
    jm.shutdown()


def test_runner_exception_becomes_error_and_queue_survives():
    def runner(job, report):
        if job.id == 1:
            raise ValueError("модель повреждена")
        return []

    jm = J.JobManager(runner)
    bad = jm.submit("k", "a", {})
    good = jm.submit("k", "b", {})
    assert _wait(lambda: good.status == J.DONE)
    assert bad.status == J.ERROR and bad.error == "модель повреждена"
    jm.shutdown()


def test_busy_models_and_history_trim():
    gate = threading.Event()
    jm = J.JobManager(_blocker(gate), max_history=2)
    a = jm.submit("k", "a", {"model_name": "A"})
    jm.submit("k", "b", {"model_name": "B"})
    assert _wait(lambda: a.status == J.RUNNING)
    assert jm.busy_models() == {"A", "B"}
    gate.set()
    assert _wait(lambda: not jm.busy_models())
    for _ in range(4):
        jm.submit("k", "x", {})
    assert _wait(lambda: all(j.status in J.FINISHED for j in jm.list()))
    jm.submit("k", "y", {})
    assert _wait(lambda: len(jm.list()) <= 3)
    jm.shutdown()


# --------------------------------------------------------------------------
# engine (подменённый ultimate_rvc)


class _F0(enum.Enum):
    RMVPE = "rmvpe"
    CREPE = "crepe"


class _Emb(enum.Enum):
    CONTENTVEC = "contentvec"
    CUSTOM = "custom"


class _Ext(enum.Enum):
    MP3 = "mp3"
    WAV = "wav"


class _Pipe:
    def __init__(self, files, stages=()):
        self.files, self.stages, self.kwargs = files, stages, None

    def run_pipeline(self, **kwargs):
        self.kwargs = kwargs
        bar = kwargs["progress_bar"]
        for i, stage in enumerate(self.stages):
            bar(i / len(self.stages), desc=stage)
        return tuple(self.files)


def _fake_mods(song=None, speech=None):
    models = type("M", (), {
        "get_voice_model_names": staticmethod(lambda: ["Anna", "Bob"]),
        "load_public_models_table": staticmethod(lambda preds: [["N", "d", ["t"], "c", "2024", "http://u"]]),
        "get_public_model_tags": staticmethod(lambda: ["t"]),
    })
    return type("Mods", (), {
        "song_cover": song or _Pipe(["/o/cover.mp3", "/o/song.mp3"]),
        "speech": speech or _Pipe(["/o/speech.mp3"]),
        "models": models, "F0Method": _F0, "EmbedderModel": _Emb, "AudioExt": _Ext,
    })


def _ready_engine(**kw):
    eng = E.Engine("/tmp/x", loader=lambda: _fake_mods(**kw))
    eng.start_warmup()
    assert _wait(lambda: eng.state == E.READY)
    return eng


def test_engine_loading_error_is_reported_not_raised():
    def boom():
        raise ImportError("No module named torch")

    eng = E.Engine("/tmp/x", loader=boom)
    eng.start_warmup()
    assert _wait(lambda: eng.state == E.ERROR)
    assert "torch" in eng.error
    with pytest.raises(RuntimeError):
        eng.mods()


def test_options_come_from_real_enum_members():
    opts = _ready_engine().options()
    assert opts["f0_methods"] == [{"value": "rmvpe", "label": "RMVPE"},
                                  {"value": "crepe", "label": "CREPE"}]
    assert opts["f0_default"] == "rmvpe" and opts["format_default"] == "mp3"


def test_to_enum_accepts_value_or_name_and_rejects_unknown():
    assert E.to_enum(_F0, "CREPE") is _F0.CREPE
    assert E.to_enum(_F0, "rmvpe") is _F0.RMVPE
    with pytest.raises(P.ParamError):
        E.to_enum(_F0, "nope")


def test_run_song_cover_maps_enums_and_progress():
    pipe = _Pipe(["/o/cover.mp3", "/o/song.mp3"], stages=["[~] Retrieving song...", "[~] Converting..."])
    eng = _ready_engine(song=pipe)
    seen = []
    files = eng.run_song_cover(
        {"source": "a.mp3", "model_name": "Anna", "f0_method": "crepe", "n_semitones": 2},
        lambda progress=None, stage=None, check_cancel=True: seen.append((progress, stage)),
    )
    assert pipe.kwargs["f0_method"] is _F0.CREPE
    assert pipe.kwargs["n_semitones"] == 2 and pipe.kwargs["model_name"] == "Anna"
    assert seen == [(0.0, "Retrieving song..."), (0.5, "Converting...")]
    assert [f["label"] for f in files] == ["Готовый кавер", "Исходная песня"]
    assert files[0]["name"] == "cover.mp3"


def test_progress_sink_cancel_propagates_out_of_pipeline():
    pipe = _Pipe(["/o/c.mp3"], stages=["[~] one", "[~] two"])
    eng = _ready_engine(song=pipe)

    def report(progress=None, stage=None, check_cancel=True):
        if stage == "two":
            raise J.JobCancelled()

    with pytest.raises(J.JobCancelled):
        eng.run_song_cover({"source": "a", "model_name": "Anna"}, report)


def test_progress_sink_handles_gradio_tuple_and_tqdm():
    got = []
    sink = E._Progress(lambda p=None, s=None, check_cancel=True: got.append((p, s)))
    sink((1, 4), desc="[~] x")
    sink(None, desc="y")
    assert list(sink.tqdm([1, 2], desc="z")) == [1, 2]
    assert got == [(0.25, "x"), (None, "y"), (None, "z")]


def test_run_speech_uses_speech_pipeline():
    pipe = _Pipe(["/o/s.mp3"])
    eng = _ready_engine(speech=pipe)
    files = eng.run_speech({"source": "/t/a.txt", "model_name": "Bob",
                            "tts_voice": "ru-RU-DmitryNeural", "output_format": "wav"},
                           lambda *a, **k: None)
    assert pipe.kwargs["tts_voice"] == "ru-RU-DmitryNeural"
    assert pipe.kwargs["output_format"] is _Ext.WAV
    assert files[0]["label"] == "Озвучка"


def test_public_models_shape():
    data = _ready_engine().public_models()
    assert data["tags"] == ["t"]
    assert data["models"][0] == {"name": "N", "description": "d", "tags": ["t"],
                                 "credit": "c", "added": "2024", "url": "http://u"}


def test_unusable_runner_result_becomes_error_not_dead_worker():
    jm = J.JobManager(lambda job, report: 42 if job.id == 1 else [])  # int -- не список
    bad = jm.submit("k", "a", {})
    good = jm.submit("k", "b", {})
    assert _wait(lambda: good.status == J.DONE)  # очередь жива
    assert bad.status == J.ERROR
    jm.shutdown()
