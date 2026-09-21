"""
Тесты генератора промптов (shared_promptgen.py + imagine/backend/promptgen.py).

Настоящий llama-server не нужен: вместо него запускается крошечный
поддельный сервер (скрипт со shebang'ом текущего интерпретатора), который
говорит на том же протоколе, что нужен бэкенду: GET /health (503, пока
«грузится», потом 200), POST /v1/chat/completions со стримингом SSE. Его
поведение задаётся JSON-файлом, который передаётся как «модель» (-m) --
так тест может управлять и скоростью, и падением, и «рассуждениями», и
подсмотреть, какие аргументы и какое тело запроса пришли.

Проверяется весь жизненный цикл, который важен пользователю: задача
запускается -> ответ попадает в результат -> процесс модели ПОГАШЕН к
моменту «готово»; отмена посреди генерации тоже гасит процесс; падение
llama-server при старте превращается в понятную ошибку с хвостом вывода.

Тесты запускаются только на POSIX (shebang-скрипт как исполняемый файл) --
Windows-специфичные части (Job Object, .exe) здесь не проверяются.
"""

import json
import os
import stat
import sys
import time

import pytest

from comfyui_studio import shared_promptgen as sp
from comfyui_studio.imagine.backend import promptgen as pg

pytestmark = pytest.mark.skipif(os.name == "nt", reason="поддельный llama-server -- shebang-скрипт")

FAKE_SERVER = r'''#!__PYTHON__
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

args = sys.argv[1:]
def arg(name, default=None):
    return args[args.index(name) + 1] if name in args else default

port = int(arg("--port"))
ctl = json.load(open(arg("-m")))
if ctl.get("crash"):
    print("fake-llama: failed to load model", flush=True)
    sys.exit(ctl["crash"])
open(ctl["pidfile"], "w").write(str(os.getpid()))
open(ctl["argsfile"], "w").write(json.dumps(args))
for line in ctl.get("print_lines", []):
    print(line, flush=True)
if ctl.get("cache_grow_mb"):
    os.makedirs(ctl["cache_dir"], exist_ok=True)
    with open(os.path.join(ctl["cache_dir"], "kernel.bin"), "wb") as f:
        f.write(b"0" * int(ctl["cache_grow_mb"] * 1048576))
if ctl.get("child"):
    import subprocess
    child = subprocess.Popen(["sleep", "300"])
    open(ctl["childfile"], "w").write(str(child.pid))
started = time.time()

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_GET(self):
        ready = time.time() - started > ctl.get("load_s", 0.3)
        body = b'{"status":"ok"}' if ready else b'{"error":{"message":"Loading model"}}'
        self.send_response(200 if ready else 503)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        n = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(n))
        open(ctl["reqfile"], "w").write(json.dumps(body))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        def chunk(data):
            self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
            self.wfile.flush()
        for i, piece in enumerate(ctl.get("pieces", ["Hello", " world"])):
            if ctl.get("die_after") is not None and i >= ctl["die_after"]:
                print("fake-llama: CUDA error: out of memory", flush=True)
                os._exit(9)
            delta = {"reasoning_content": piece[2:]} if piece.startswith("R:") else {"content": piece}
            chunk(("data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n\n").encode())
            time.sleep(ctl.get("delay", 0.0))
        if ctl.get("timings"):
            last = {"choices": [{"delta": {}, "finish_reason": "stop"}], "timings": ctl["timings"]}
            chunk(("data: " + json.dumps(last) + "\n\n").encode())
        chunk(b"data: [DONE]\n\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
'''


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    """llama_dir с поддельным llama-server, «модель»-файл управления и
    изолированный prompt_generator.json."""
    monkeypatch.setattr(sp, "SHARED_DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(sp, "SHARED_PROMPTGEN_PATH", str(tmp_path / "shared" / "prompt_generator.json"))
    monkeypatch.setattr(sp, "LOG_DIR", str(tmp_path / "logs"))

    llama_dir = tmp_path / "llama"
    llama_dir.mkdir()
    exe = llama_dir / "llama-server"
    exe.write_text(FAKE_SERVER.replace("__PYTHON__", sys.executable), encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)

    files = {
        "pidfile": str(tmp_path / "pid.txt"),
        "argsfile": str(tmp_path / "args.json"),
        "reqfile": str(tmp_path / "req.json"),
        "childfile": str(tmp_path / "child.txt"),
    }
    model = tmp_path / "model.gguf"

    def configure(control=None, **settings):
        model.write_text(json.dumps({**files, **(control or {})}), encoding="utf-8")
        cfg = {"llama_dir": str(llama_dir), "model_path": str(model)}
        cfg.update(settings)
        assert sp.write_settings(cfg)

    class Env:
        pass

    e = Env()
    e.tmp = tmp_path
    e.llama_dir = llama_dir
    e.model = model
    e.files = files
    e.configure = configure
    e.request = lambda: json.loads(open(files["reqfile"], encoding="utf-8").read())
    e.args = lambda: json.loads(open(files["argsfile"], encoding="utf-8").read())
    e.pid = lambda: int(open(files["pidfile"], encoding="utf-8").read())
    e.child_pid = lambda: int(open(files["childfile"], encoding="utf-8").read())
    return e


@pytest.fixture
def gen():
    g = pg.PromptGenerator()
    yield g
    g.shutdown()
    # дожидаемся хвоста задачи (запись "после остановки" в finally), чтобы
    # он не попал в лог следующего теста
    job = g._job
    if job is not None and job.thread is not None:
        job.thread.join(10)


def wait_finished(gen, job_id, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = gen.get_job(job_id)
        if snap["state"] not in ("loading", "generating"):
            return snap
        time.sleep(0.05)
    raise AssertionError(f"задача не завершилась: {gen.get_job(job_id)}")


def process_gone(pid, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        # зомби (ещё не reaped) тоже считается «ушедшим» -- смотрим /proc
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
                if f.read().split(") ")[-1].startswith("Z"):
                    return True
        except OSError:
            return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------------------
# shared_promptgen: чистые функции
# ---------------------------------------------------------------------------

def test_compose_prompt_placeholder_substitution():
    out = sp.compose_prompt('Intro\nUser: "{input}"', "a cat")
    assert out == 'Intro\nUser: "a cat"'


def test_compose_prompt_without_placeholder_appends_after_prefix():
    assert sp.compose_prompt("Describe:", "a cat") == "Describe:\na cat"
    assert sp.compose_prompt("Describe: ", "a cat") == "Describe: a cat"


def test_compose_prompt_braces_in_prefix_are_not_format_fields():
    out = sp.compose_prompt('Return {"a": 1}. User: {input}', "x")
    assert out == 'Return {"a": 1}. User: x'


def test_compose_prompt_image_only_uses_fallback_text():
    assert sp.IMAGE_ONLY_TEXT in sp.compose_prompt(sp.DEFAULT_PREFIX, "  ", has_image=True)
    assert sp.compose_prompt("P: {input}", "", has_image=False) == "P: "


def test_default_prefix_matches_requested_wording():
    assert sp.DEFAULT_PREFIX.startswith("Write a single medium-length paragraph")
    assert sp.DEFAULT_PREFIX.endswith('User: "{input}"')
    assert "Output only the image description" in sp.DEFAULT_PREFIX


def test_settings_roundtrip_and_defaults(env):
    assert sp.read_settings()["prefix"] == sp.DEFAULT_PREFIX
    assert sp.write_settings({"llama_dir": "C:\\x", "ctx_size": "4096", "bogus": 1})
    cfg = sp.read_settings()
    assert cfg["llama_dir"] == "C:\\x"
    assert cfg["ctx_size"] == 4096
    assert "bogus" not in cfg


def test_settings_corrupt_file_falls_back_to_defaults(env):
    os.makedirs(sp.SHARED_DIR, exist_ok=True)
    with open(sp.SHARED_PROMPTGEN_PATH, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert sp.read_settings() == sp.DEFAULTS


def test_validate_messages(env):
    assert "папка llama.cpp" in sp.validate({})
    assert "не найдена" in sp.validate({"llama_dir": str(env.tmp / "nope")})
    empty = env.tmp / "empty"
    empty.mkdir()
    assert "нет llama-server" in sp.validate({"llama_dir": str(empty)})
    assert "модели" in sp.validate({"llama_dir": str(env.llama_dir)})
    assert "не найден" in sp.validate({"llama_dir": str(env.llama_dir), "model_path": str(env.tmp / "x.gguf")})
    env.configure()
    ok = {"llama_dir": str(env.llama_dir), "model_path": str(env.model)}
    assert sp.validate(ok) is None
    assert "mmproj" in sp.validate({**ok, "mmproj_path": str(env.tmp / "nope.gguf")})


def test_dll_dirs_finds_lm_studio_vendor_folder(tmp_path):
    backends = tmp_path / "backends"
    llama = backends / "llama.cpp-win-x86_64-nvidia-cuda12-avx2-2.41.0"
    vendor = backends / "vendor" / "win-llama-cuda12-vendor-v2"
    empty_vendor = backends / "vendor" / "no-dlls-here"
    for d in (llama, vendor, empty_vendor):
        d.mkdir(parents=True)
    (vendor / "cudart64_12.dll").write_bytes(b"")
    dirs = sp.dll_dirs({"llama_dir": str(llama)})
    assert dirs == [str(llama), str(vendor)]


def test_dll_dirs_includes_extra_dir_once(tmp_path):
    llama = tmp_path / "llama"
    extra = tmp_path / "extra"
    llama.mkdir()
    extra.mkdir()
    dirs = sp.dll_dirs({"llama_dir": str(llama), "extra_dll_dir": str(extra)})
    assert dirs == [str(llama), str(extra)]
    assert sp.dll_dirs({"llama_dir": str(llama), "extra_dll_dir": str(llama)}) == [str(llama)]


# ---------------------------------------------------------------------------
# promptgen: очистка и команда
# ---------------------------------------------------------------------------

def test_clean_output_strips_reasoning():
    assert pg.clean_output("<think>hmm</think>\n\nA cat.") == "A cat."
    assert pg.clean_output("A cat. <think>unfinished") == "A cat."
    assert pg.clean_output("<THINK>x</THINK>Y") == "Y"
    assert pg.clean_output("  plain  ") == "plain"


def test_build_command_flags(env):
    env.configure(
        mmproj_path=str(env.tmp / "mmproj.gguf"),
        ctx_size=8192,
        gpu_layers="99",
        extra_args='--reasoning off --alias "my model"',
    )
    cfg = sp.read_settings()
    cmd = pg.build_command(cfg, 5555)
    assert cmd[0].endswith("llama-server")
    assert cmd[cmd.index("-m") + 1] == str(env.model)
    assert cmd[cmd.index("--port") + 1] == "5555"
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert cmd[cmd.index("-c") + 1] == "8192"
    assert cmd[cmd.index("-ngl") + 1] == "99"
    assert cmd[cmd.index("--mmproj") + 1] == str(env.tmp / "mmproj.gguf")
    assert "--reasoning" in cmd and "off" in cmd
    assert "my model" in cmd  # кавычки в «Доп. аргументах» разобраны


def test_build_command_omits_optional_flags(env):
    env.configure(ctx_size=0)
    cmd = pg.build_command(sp.read_settings(), 1)
    for flag in ("-c", "-ngl", "--mmproj"):
        assert flag not in cmd


def test_build_env_prepends_dll_dirs(env, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    env.configure()
    path = pg.build_env(sp.read_settings())["PATH"]
    assert path.split(os.pathsep)[0] == str(env.llama_dir)
    assert path.endswith("/usr/bin")


# ---------------------------------------------------------------------------
# жизненный цикл задачи
# ---------------------------------------------------------------------------

def test_text_job_end_to_end_stops_model(env, gen):
    env.configure({"pieces": ["A red", " fox", " jumps."]})
    job_id = gen.start("лиса в лесу", None)
    snap = wait_finished(gen, job_id)

    assert snap["state"] == "done", snap
    assert snap["text"] == "A red fox jumps."
    assert snap["tokens"] == 3
    assert snap["error"] is None

    # запрос: одно user-сообщение "префикс + текст", 8192 токена, стриминг
    req = env.request()
    assert req["max_tokens"] == 8192
    assert req["stream"] is True
    assert len(req["messages"]) == 1 and req["messages"][0]["role"] == "user"
    content = req["messages"][0]["content"]
    assert isinstance(content, str)
    assert content.startswith("Write a single medium-length paragraph")
    assert content.endswith('User: "лиса в лесу"')

    # к моменту "готово" процесс модели уже погашен
    assert process_gone(env.pid())


def test_image_job_sends_image_url_and_mmproj(env, gen):
    env.configure({"pieces": ["ok"]}, mmproj_path=str(env.tmp / "mmproj.gguf"))
    (env.tmp / "mmproj.gguf").write_text("x")
    data_url = "data:image/jpeg;base64,/9j/AAAA"
    job_id = gen.start("", data_url)
    snap = wait_finished(gen, job_id)
    assert snap["state"] == "done", snap

    content = env.request()["messages"][0]["content"]
    assert content[0] == {"type": "image_url", "image_url": {"url": data_url}}
    assert content[1]["type"] == "text"
    assert sp.IMAGE_ONLY_TEXT in content[1]["text"]
    assert "--mmproj" in env.args()
    assert process_gone(env.pid())


def test_reasoning_is_not_included_in_result(env, gen):
    env.configure({"pieces": ["R:let me think", "<think>more</think>", "Final answer."]})
    snap = wait_finished(gen, gen.start("x", None))
    assert snap["state"] == "done"
    assert snap["text"] == "Final answer."


def test_only_reasoning_gives_helpful_error(env, gen):
    env.configure({"pieces": ["R:thinking", "R:still thinking"]})
    snap = wait_finished(gen, gen.start("x", None))
    assert snap["state"] == "error"
    assert "пустой ответ" in snap["error"]
    assert "--reasoning off" in snap["error"]
    assert process_gone(env.pid())


def test_progress_is_visible_while_generating(env, gen):
    env.configure({"pieces": ["one ", "two ", "three"], "delay": 0.4})
    job_id = gen.start("x", None)
    seen_partial = False
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        snap = gen.get_job(job_id)
        if snap["state"] == "generating" and snap["text"]:
            seen_partial = True
            assert not snap["text"].endswith("three")
            break
        if snap["state"] not in ("loading", "generating"):
            break
        time.sleep(0.05)
    assert seen_partial, "промежуточный текст не появился в статусе"
    assert wait_finished(gen, job_id)["text"] == "one two three"


def test_server_crash_at_startup_reports_output_tail(env, gen):
    env.configure({"crash": 3})
    snap = wait_finished(gen, gen.start("x", None))
    assert snap["state"] == "error"
    assert "код 3" in snap["error"]
    assert "failed to load model" in snap["error"]


def test_server_dying_mid_generation_is_an_error_not_a_result(env, gen):
    env.configure({"pieces": ["Half", " a", " sentence", " that"], "die_after": 2})
    snap = wait_finished(gen, gen.start("x", None))
    assert snap["state"] == "error", snap
    assert "во время генерации" in snap["error"]
    assert "code 9" in snap["error"] or "код 9" in snap["error"]
    assert "out of memory" in snap["error"]


def test_cancel_during_generation_kills_process(env, gen):
    env.configure({"pieces": ["w"] * 200, "delay": 0.2})
    job_id = gen.start("x", None)
    deadline = time.monotonic() + 20
    while gen.get_job(job_id)["state"] != "generating":
        assert time.monotonic() < deadline
        time.sleep(0.05)
    pid = env.pid()

    assert gen.cancel(job_id) is True
    snap = wait_finished(gen, job_id)
    assert snap["state"] == "cancelled"
    assert snap["error"] is None
    assert process_gone(pid)


def test_cancel_while_model_loading(env, gen):
    env.configure({"load_s": 60})
    job_id = gen.start("x", None)
    time.sleep(1.0)  # процесс запущен, модель "грузится"
    assert gen.get_job(job_id)["state"] == "loading"
    assert gen.cancel(job_id) is True
    snap = wait_finished(gen, job_id)
    assert snap["state"] == "cancelled"
    assert process_gone(env.pid())


def test_second_start_while_busy_is_rejected(env, gen):
    env.configure({"pieces": ["w"] * 100, "delay": 0.2})
    first = gen.start("x", None)
    with pytest.raises(pg.PromptGenError) as exc:
        gen.start("y", None)
    assert exc.value.status_code == 409
    gen.cancel(first)
    wait_finished(gen, first)
    # после завершения можно снова
    env.configure({"pieces": ["ok"]})
    assert wait_finished(gen, gen.start("z", None))["state"] == "done"


def test_start_validation_errors(env, gen):
    with pytest.raises(pg.PromptGenError) as exc:
        gen.start("x", None)  # ничего не настроено
    assert exc.value.status_code == 400

    env.configure()
    with pytest.raises(pg.PromptGenError, match="Введите текст"):
        gen.start("   ", None)
    with pytest.raises(pg.PromptGenError, match="mmproj"):
        gen.start("x", "data:image/png;base64,AAAA")  # mmproj не задан
    env.configure(mmproj_path=str(env.tmp / "m.gguf"))
    (env.tmp / "m.gguf").write_text("x")
    with pytest.raises(pg.PromptGenError, match="data:image"):
        gen.start("x", "http://evil/x.png")


def test_status_reports_configuration(env, gen):
    st = gen.status()
    assert st["available"] and not st["configured"] and st["job"] is None

    env.configure()
    st = gen.status()
    assert st["configured"] and st["problem"] is None and st["vision"] is False

    (env.tmp / "m.gguf").write_text("x")
    env.configure(mmproj_path=str(env.tmp / "m.gguf"))
    assert gen.status()["vision"] is True

    env.configure(llama_dir=str(env.tmp / "gone"))
    st = gen.status()
    assert st["configured"] and "не найдена" in st["problem"]


def test_free_comfy_hook_called_only_when_enabled(env, gen):
    calls = []
    env.configure({"pieces": ["ok"]})
    wait_finished(gen, gen.start("x", None, pre_start=lambda: calls.append(1)))
    assert calls == []

    env.configure({"pieces": ["ok"]}, free_comfy_mode="always")
    wait_finished(gen, gen.start("x", None, pre_start=lambda: calls.append(1)))
    assert calls == [1]


def test_free_comfy_hook_failure_does_not_block_generation(env, gen):
    env.configure({"pieces": ["ok"]}, free_comfy_mode="always")

    def boom():
        raise RuntimeError("comfy is down")

    assert wait_finished(gen, gen.start("x", None, pre_start=boom))["state"] == "done"


def test_shutdown_kills_running_model(env, gen):
    env.configure({"pieces": ["w"] * 200, "delay": 0.2})
    job_id = gen.start("x", None)
    deadline = time.monotonic() + 20
    while gen.get_job(job_id)["state"] != "generating":
        assert time.monotonic() < deadline
        time.sleep(0.05)
    pid = env.pid()
    gen.shutdown()
    assert process_gone(pid)
    assert wait_finished(gen, job_id)["state"] in ("cancelled", "error")


# ---------------------------------------------------------------------------
# HTTP-слой (FastAPI)
# ---------------------------------------------------------------------------

def test_http_endpoints(env, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from comfyui_studio.imagine.backend import main

    fresh = pg.PromptGenerator()
    monkeypatch.setattr(pg, "generator", fresh)
    monkeypatch.setattr(main.promptgen, "generator", fresh)
    client = TestClient(main.app)  # без `with`: startup-хуки (WS-форвардер) не нужны

    try:
        r = client.get("api/promptgen/status")
        assert r.status_code == 200 and r.json()["configured"] is False

        r = client.post("api/promptgen/start", json={"text": "cat"})
        assert r.status_code == 400 and "llama.cpp" in r.json()["detail"]

        env.configure({"pieces": ["A ", "cat."]})
        r = client.get("api/promptgen/status")
        assert r.json()["configured"] is True and r.json()["problem"] is None

        r = client.post("api/promptgen/start", json={"text": "cat"})
        assert r.status_code == 200
        job_id = r.json()["job_id"]

        deadline = time.monotonic() + 20
        snap = {}
        while time.monotonic() < deadline:
            snap = client.get(f"api/promptgen/job/{job_id}").json()
            if snap["state"] not in ("loading", "generating"):
                break
            time.sleep(0.1)
        assert snap["state"] == "done" and snap["text"] == "A cat."

        assert client.get("api/promptgen/job/nope").status_code == 404
        assert client.post(f"api/promptgen/job/{job_id}/cancel").json() == {"ok": False}
    finally:
        fresh.shutdown()


# ---------------------------------------------------------------------------
# логирование, диагностика, устойчивость
# ---------------------------------------------------------------------------

def wait_log(substr, timeout=8):
    """Ждёт появления подстроки в promptgen.log (часть записей делается
    уже после смены статуса задачи -- в блоке finally)."""
    deadline = time.monotonic() + timeout
    text = ""
    while time.monotonic() < deadline:
        try:
            with open(sp.log_file_path(), encoding="utf-8") as f:
                text = f.read()
        except OSError:
            text = ""
        if substr in text:
            return text
        time.sleep(0.05)
    raise AssertionError(f"в promptgen.log не появилось {substr!r}:\n{text}")


def fake_gpu(free_mb, total_mb=12000):
    return lambda: [{"index": 0, "name": "Fake GPU", "total_mb": total_mb,
                     "used_mb": total_mb - free_mb, "free_mb": free_mb}]


def test_job_writes_timeline_to_promptgen_log(env, gen):
    env.configure({"pieces": ["A", " fox"], "print_lines": ["load_tensors: offloaded 33/33 layers to GPU"]})
    job_id = gen.start("секретный текст запроса", None)
    assert wait_finished(gen, job_id)["state"] == "done"
    text = wait_log(f"[{job_id}] после остановки")

    for expected in (
        f"[{job_id}] старт: текст 23 симв., изображение: нет",
        f"[{job_id}] настройки:",
        f"[{job_id}] перед запуском:",
        f"[{job_id}] llama-server запущен: pid=",
        f"[{job_id}] модель загружена за",
        "слои на GPU: 33 из 33",
        f"[{job_id}] после загрузки:",
        f"[{job_id}] тайминги: загрузка",
        f"[{job_id}] llama-server остановлен: pid",
        f"[{job_id}] готово за",
        f"[{job_id}] после остановки:",
    ):
        assert expected in text, expected
    # содержимое запроса и ответа в лог не пишется -- только длины
    assert "секретный текст" not in text
    assert "A fox" not in text


def test_llama_server_output_is_saved_per_run_and_pruned(env, gen):
    llama_logs = sp.llama_log_dir()
    os.makedirs(llama_logs)
    for i in range(30):  # старые логи прошлых запусков
        path = os.path.join(llama_logs, f"old-{i:02d}.log")
        open(path, "w").write("x")
        os.utime(path, (1000 + i, 1000 + i))

    env.configure({"pieces": ["ok"], "print_lines": ["fake-llama: hello from the model"]})
    job_id = gen.start("x", None)
    assert wait_finished(gen, job_id)["state"] == "done"

    files = [f for f in os.listdir(llama_logs) if f.endswith(".log")]
    assert len(files) <= sp.LLAMA_LOGS_KEEP
    mine = [f for f in files if job_id in f]
    assert len(mine) == 1
    content = open(os.path.join(llama_logs, mine[0]), encoding="utf-8").read()
    assert "# cmd:" in content and "fake-llama: hello from the model" in content
    assert "old-00.log" not in files  # самые старые удалены, свежие остались
    assert "old-29.log" in files


def test_error_message_points_to_log_file(env, gen):
    env.configure({"crash": 3})
    snap = wait_finished(gen, gen.start("x", None))
    assert snap["state"] == "error"
    assert sp.log_file_path() in snap["error"]
    assert "код 3" in snap["error"]
    assert "ошибка:" in wait_log("ошибка:")


def test_llama_timings_are_logged(env, gen):
    env.configure({
        "pieces": ["a", "b"],
        "timings": {"prompt_n": 1834, "prompt_ms": 91700.0, "prompt_per_second": 20.0,
                    "predicted_n": 2, "predicted_ms": 100.0, "predicted_per_second": 20.0},
    })
    assert wait_finished(gen, gen.start("x", None))["state"] == "done"
    text = wait_log("llama-server timings")
    assert "запрос 1834 ток. за 91700 мс (20.0 ток/с" in text


def test_partial_gpu_offload_and_cpu_clip_produce_warnings(env, gen):
    (env.tmp / "mm.gguf").write_text("x")
    env.configure(
        {"pieces": ["ok"], "print_lines": [
            "load_tensors: offloaded 12/33 layers to GPU",
            "clip_ctx: CLIP using CPU backend",
        ]},
        mmproj_path=str(env.tmp / "mm.gguf"),
    )
    snap = wait_finished(gen, gen.start("x", None))
    assert snap["state"] == "done"
    assert {"code": "gpu_partial", "loaded": 12, "total": 33} in snap["warnings"]
    assert {"code": "clip_cpu"} in snap["warnings"]
    text = wait_log("после остановки")
    assert "WARNING" in text and "12 из 33 слоёв" in text and "кодировщик изображений работает на CPU" in text


def test_full_offload_gives_no_warnings(env, gen):
    env.configure({"pieces": ["ok"], "print_lines": ["load_tensors: offloaded 33/33 layers to GPU"]})
    assert wait_finished(gen, gen.start("x", None))["warnings"] == []


def test_llama_process_tree_is_killed_including_children(env, gen):
    """Обёртка llama-server могла бы оставить потомка жить (и держать VRAM):
    убиваться должно всё дерево, а не только головной pid."""
    env.configure({"pieces": ["ok"], "child": True})
    assert wait_finished(gen, gen.start("x", None))["state"] == "done"
    assert process_gone(env.pid())
    assert process_gone(env.child_pid()), "потомок llama-server остался жить"


def test_cancel_kills_children_too(env, gen):
    env.configure({"pieces": ["w"] * 200, "delay": 0.2, "child": True})
    job_id = gen.start("x", None)
    deadline = time.monotonic() + 20
    while gen.get_job(job_id)["state"] != "generating":
        assert time.monotonic() < deadline
        time.sleep(0.05)
    gen.cancel(job_id)
    assert wait_finished(gen, job_id)["state"] == "cancelled"
    assert process_gone(env.pid()) and process_gone(env.child_pid())


def test_running_process_is_registered_and_unregistered(env, gen):
    env.configure({"pieces": ["w"] * 50, "delay": 0.1})
    job_id = gen.start("x", None)
    deadline = time.monotonic() + 20
    while gen.get_job(job_id)["state"] != "generating":
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert [e["pid"] for e in pg._registry_read()] == [env.pid()]
    gen.cancel(job_id)
    wait_finished(gen, job_id)
    wait_log(f"[{job_id}] после остановки")
    assert pg._registry_read() == []


def _spawn_sleeper(tmp_path, name):
    script = tmp_path / name
    script.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(300)\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    import subprocess
    return subprocess.Popen([str(script)])


def test_sweep_stale_kills_orphaned_llama_server(env):
    orphan = _spawn_sleeper(env.tmp, "llama-server")
    try:
        pg._register_process(orphan, "dead-job")
        assert [e["pid"] for e in pg._registry_read()] == [orphan.pid]
        assert pg.sweep_stale() == 1
        assert process_gone(orphan.pid)
        assert pg._registry_read() == []
    finally:
        orphan.kill()
        orphan.wait()


def test_sweep_stale_ignores_reused_pid_and_foreign_processes(env):
    innocent = _spawn_sleeper(env.tmp, "llama-server")      # имя похоже, но время создания не то
    other = _spawn_sleeper(env.tmp, "not-related-tool")     # время создания то, но имя чужое
    try:
        pg._register_process(innocent, "job-a")
        pg._register_process(other, "job-b")
        entries = pg._registry_read()
        for e in entries:
            if e["pid"] == innocent.pid:
                e["create_time"] -= 3600  # pid «переиспользован» другим процессом
        pg._registry_write(entries)
        assert pg.sweep_stale() == 0
        assert innocent.poll() is None and other.poll() is None
    finally:
        for p in (innocent, other):
            p.kill()
            p.wait()


def test_start_recovers_when_active_job_thread_is_dead(env, gen):
    """Задача числится активной, а поток умер -- раньше это выглядело бы как
    вечное «генератор уже работает» до перезапуска Studio."""
    import threading
    env.configure({"pieces": ["ok"]})
    ghost = pg.Job()
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    ghost.thread = dead
    gen._job = ghost
    job_id = gen.start("x", None)
    assert job_id != ghost.id
    assert ghost.state == "error"
    assert wait_finished(gen, job_id)["state"] == "done"
    assert "сбрасываю" in wait_log("сбрасываю")


def test_repeated_generations_leave_no_processes_behind(env, gen):
    """Сценарий из жалобы «после нескольких генераций перестало работать»:
    серия запусков подряд, каждый обязан гасить и процесс, и потомка, и
    освобождать реестр."""
    pids = []
    for i in range(4):
        env.configure({"pieces": [f"answer {i}"], "child": True})
        job_id = gen.start(f"q{i}", None)
        snap = wait_finished(gen, job_id)
        assert snap["state"] == "done" and snap["text"] == f"answer {i}"
        pids += [env.pid(), env.child_pid()]
    assert all(process_gone(p) for p in pids)
    wait_log(f"[{job_id}] после остановки")
    assert pg._registry_read() == []


# -- решение о выгрузке ComfyUI ------------------------------------------------

def test_estimate_vram_counts_model_mmproj_and_context(env):
    with open(env.tmp / "big.gguf", "wb") as f:
        f.truncate(1000 * 1024 * 1024)
    with open(env.tmp / "mm.gguf", "wb") as f:
        f.truncate(200 * 1024 * 1024)
    est = pg.estimate_vram_mb({"model_path": str(env.tmp / "big.gguf"),
                               "mmproj_path": str(env.tmp / "mm.gguf"), "ctx_size": 10000})
    assert est == int(1200 * 1.05 + 512 + 10000 * 0.06)


def test_auto_mode_frees_comfy_only_when_vram_is_short(env, gen, monkeypatch):
    calls = []
    env.configure({"pieces": ["ok"]}, ctx_size=65536)  # нужно ≈ 4.4 ГБ
    monkeypatch.setattr(pg, "gpu_memory", fake_gpu(free_mb=1000))
    wait_finished(gen, gen.start("x", None, pre_start=lambda: calls.append("freed")))
    assert calls == ["freed"]
    text = wait_log("после остановки")
    assert "auto: свободно 1000 МиБ < нужно" in text and "выгружаю модели ComfyUI" in text

    calls.clear()
    monkeypatch.setattr(pg, "gpu_memory", fake_gpu(free_mb=20000, total_mb=24000))
    wait_finished(gen, gen.start("x", None, pre_start=lambda: calls.append("freed")))
    assert calls == []
    assert "выгрузка ComfyUI не требуется" in wait_log("выгрузка ComfyUI не требуется")


def test_auto_mode_without_gpu_metrics_leaves_comfy_alone(env, gen, monkeypatch):
    calls = []
    env.configure({"pieces": ["ok"]}, ctx_size=65536)
    monkeypatch.setattr(pg, "gpu_memory", lambda: None)
    wait_finished(gen, gen.start("x", None, pre_start=lambda: calls.append(1)))
    assert calls == []
    assert "VRAM не измеряется" in wait_log("VRAM не измеряется")


def test_never_mode_never_frees_even_when_vram_is_short(env, gen, monkeypatch):
    calls = []
    env.configure({"pieces": ["ok"]}, ctx_size=65536, free_comfy_mode="never")
    monkeypatch.setattr(pg, "gpu_memory", fake_gpu(free_mb=100))
    wait_finished(gen, gen.start("x", None, pre_start=lambda: calls.append(1)))
    assert calls == []


def test_always_mode_frees_even_when_vram_is_plenty(env, gen, monkeypatch):
    calls = []
    env.configure({"pieces": ["ok"]}, free_comfy_mode="always")
    monkeypatch.setattr(pg, "gpu_memory", fake_gpu(free_mb=20000, total_mb=24000))
    wait_finished(gen, gen.start("x", None, pre_start=lambda: calls.append(1)))
    assert calls == [1]


def test_generator_waits_for_vram_after_previous_stop(env, gen, monkeypatch):
    """Драйвер освобождает память убитого процесса не мгновенно: следующий
    запуск сразу после предыдущего должен дождаться, иначе --fit увидит
    «занятую» память и отдаст часть слоёв на CPU."""
    readings = iter([2000, 4000, 6000, 6000, 6000, 6000, 6000, 6000, 6000, 6000, 6000, 6000])
    last = {"v": 6000}

    def gpu():
        try:
            last["v"] = next(readings)
        except StopIteration:
            pass
        return [{"index": 0, "name": "Fake", "total_mb": 24000, "used_mb": 24000 - last["v"], "free_mb": last["v"]}]

    env.configure({"pieces": ["ok"]}, free_comfy_mode="never")
    wait_finished(gen, gen.start("x", None))          # первый запуск (gpu_memory ещё не подменена)
    monkeypatch.setattr(pg, "gpu_memory", gpu)
    wait_finished(gen, gen.start("y", None))          # второй -- сразу после первого
    text = wait_log("после остановки")
    assert "предыдущая модель остановлена" in text and "жду, пока освободится VRAM" in text


# -- настройки -------------------------------------------------------------------

def test_new_settings_defaults_and_legacy_migration(env):
    cfg = sp.read_settings()
    assert cfg["free_comfy_mode"] == "auto"
    assert cfg["image_max_side"] == 1024
    assert cfg["ctx_size"] == 8192

    os.makedirs(sp.SHARED_DIR, exist_ok=True)
    def write(d):
        with open(sp.SHARED_PROMPTGEN_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f)
    write({"free_comfy_first": True})
    assert sp.read_settings()["free_comfy_mode"] == "always"
    write({"free_comfy_first": False})
    assert sp.read_settings()["free_comfy_mode"] == "auto"
    write({"free_comfy_mode": "never", "free_comfy_first": True})
    assert sp.read_settings()["free_comfy_mode"] == "never"
    write({"free_comfy_mode": "bogus", "image_max_side": 5})
    cfg = sp.read_settings()
    assert cfg["free_comfy_mode"] == "auto" and cfg["image_max_side"] == 1024


def test_status_reports_image_max_side(env, gen):
    env.configure(image_max_side=768)
    assert gen.status()["image_max_side"] == 768


def test_first_version_default_ctx_is_migrated_once(env):
    os.makedirs(sp.SHARED_DIR, exist_ok=True)

    def write(d):
        with open(sp.SHARED_PROMPTGEN_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f)

    write({"ctx_size": 16384, "llama_dir": "x"})              # файл первой версии
    assert sp.read_settings()["ctx_size"] == 8192
    write({"ctx_size": 4096})                                   # другое значение не трогаем
    assert sp.read_settings()["ctx_size"] == 4096
    write({"ctx_size": 16384, "settings_version": 2})           # осознанный выбор после миграции
    assert sp.read_settings()["ctx_size"] == 16384

    # сохранение через write_settings не должно «мигрировать» только что выбранное
    assert sp.write_settings({"ctx_size": 16384})
    assert sp.read_settings()["ctx_size"] == 16384
    assert json.load(open(sp.SHARED_PROMPTGEN_PATH, encoding="utf-8"))["settings_version"] == 2


# ---------------------------------------------------------------------------
# диагностика загрузки модели
# ---------------------------------------------------------------------------

TIMESTAMPED_OUTPUT = [
    "0.00.000.120 I main: build info",
    "no timestamp on this line",
    "0.00.412.000 I load_tensors: loading",
    "0.02.010.500 I load_tensors: done",
    "5.56.717.660 I srv    load_model: loaded multimodal model, 'x.gguf'",
    "5.56.900.000 I srv    load_model: ready",
]


def test_timeline_analysis_finds_the_long_pauses():
    gaps = pg.analyze_load_timeline("\n".join(TIMESTAMPED_OUTPUT))
    # метки [М.сс.мс.мкс]: 0.02.010 = 2.01 с, 5.56.717 = 5 мин 56.7 с
    assert gaps[0].startswith("пауза 354.7 с")
    assert "load_tensors: done" in gaps[0] and "loaded multimodal model" in gaps[0]
    assert gaps[1].startswith("пауза 1.6 с")
    assert len(gaps) == 2  # остальные паузы короче порога в 1 с
    assert pg.analyze_load_timeline("plain line\nother") == []
    assert pg._line_seconds("5.56.717.660 I srv") == pytest.approx(356.71766)
    assert pg._line_seconds("garbage") is None


def test_timeline_and_key_lines_are_logged(env, gen):
    env.configure({"pieces": ["ok"], "print_lines": TIMESTAMPED_OUTPUT})
    assert wait_finished(gen, gen.start("x", None))["state"] == "done"
    text = wait_log("самые долгие паузы")
    import re
    assert re.search(r"вывод llama-server при загрузке: \d+ строк", text)  # + заголовок файла лога
    assert "пауза 354.7 с между" in text


def test_load_progress_includes_process_probe(env, gen, monkeypatch):
    monkeypatch.setattr(pg, "_FIRST_REPORT_S", 0.2)
    monkeypatch.setattr(pg, "_REPORT_EVERY_S", 0.3)
    env.configure({"pieces": ["ok"], "load_s": 1.6})
    assert wait_finished(gen, gen.start("x", None))["state"] == "done"
    text = wait_log("модель загружена за")
    lines = [ln for ln in text.splitlines() if "…модель загружается" in ln]
    assert len(lines) >= 2
    assert "CPU процесса" in lines[0] and "RSS" in lines[0] and "RAM свободно" in lines[0]


def test_cuda_kernel_cache_growth_is_reported(env, gen, monkeypatch):
    cache = env.tmp / "nvcache"
    cache.mkdir()
    monkeypatch.setenv("CUDA_CACHE_PATH", str(cache))
    env.configure({"pieces": ["ok"], "cache_dir": str(cache), "cache_grow_mb": 6})
    assert wait_finished(gen, gen.start("x", None))["state"] == "done"
    text = wait_log("кэш CUDA-ядер вырос")
    assert "вырос на 6 МиБ" in text and "JIT" in text


def test_unchanged_cuda_kernel_cache_is_reported(env, gen, monkeypatch):
    cache = env.tmp / "nvcache"
    cache.mkdir()
    monkeypatch.setenv("CUDA_CACHE_PATH", str(cache))
    env.configure({"pieces": ["ok"]})
    assert wait_finished(gen, gen.start("x", None))["state"] == "done"
    assert "JIT-компиляции не было" in wait_log("JIT-компиляции не было")


def test_missing_cuda_cache_dir_is_silently_skipped(env, gen, monkeypatch):
    monkeypatch.setenv("CUDA_CACHE_PATH", str(env.tmp / "does-not-exist"))
    env.configure({"pieces": ["ok"]})
    assert wait_finished(gen, gen.start("x", None))["state"] == "done"
    assert "кэш CUDA-ядер" not in wait_log("модель загружена за")
