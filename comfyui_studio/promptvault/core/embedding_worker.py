"""
Подпроцесс-воркер семантического поиска PromptVault.

ПОЧЕМУ ОТДЕЛЬНЫЙ ПРОЦЕСС, А НЕ ПРОСТО get_model()/unload_model() В
ЭТОМ ЖЕ ПРОЦЕССЕ (как было раньше) -- см. запись в дорожной карте
рефакторинга от 2026-08-20 с реальными цифрами из живого прогона:
`import sentence_transformers` (тянет torch) стоит ~475 МБ, а сама
модель (all-MiniLM-L6-v2) — всего ~70 МБ. При закрытии PromptVault
`_model = None` + `gc.collect()` (и даже принудительный Windows
EmptyWorkingSet) освобождают только эти ~70 МБ — оставшиеся ~475 МБ
"застревают" в процессе NАВСЕГДА, пока жив ComfyUIStudio: это не
освобождённая-но-не-возвращённая память, а ЖИВОЕ внутреннее состояние
рантайма torch (пул потоков intra-op parallelism, буферы MKL/OpenMP,
таблицы диспетчера операций ATen) — оно остаётся referenced, пока
модуль `torch` импортирован в процессе, а Python не умеет чисто
"выгрузить" уже импортированный C-экстеншен обратно.

Единственный способ гарантированно вернуть ОС ВСЮ эту память — не
импортировать torch в основном процессе ВООБЩЕ, а изолировать его в
подпроцесс, который можно реально ЗАВЕРШИТЬ (terminate/kill), когда
семантический поиск больше не нужен (закрытие окна PromptVault — см.
embedding_ipc.WorkerHandle.terminate(), вызывается из
embedding.unload_model()). Завершение процесса ОС гарантированно
забирает 100% его памяти — в отличие от dereference+gc.collect()
внутри одного общего процесса.

ПРОТОКОЛ: построчный JSON через stdin/stdout — по одному запросу/ответу
на строку, без дополнительных зависимостей поверх subprocess.Popen.
Команды:
  {"cmd": "ping"}                                   -> {"ok": true}
  {"cmd": "gpu_available"}                          -> {"ok": true, "available": bool}
  {"cmd": "load", "model_name", "device_preference",
   "query_prefix", "max_seq_length"}                -> {"ok": true, "device", "fell_back_to_cpu"}
                                                      | {"ok": false, "error", "traceback"}
  {"cmd": "encode", "texts": [...], "batch_size"}    -> {"ok": true, "shape": [N, dim], "data_b64": "..."}
                                                      | {"ok": false, "error", "traceback"}
  {"cmd": "quit"}                                    -> {"ok": true}, затем процесс завершается сам

"data_b64" — base64 от N*dim float32 в C-порядке (см. numpy.tobytes()) —
компактнее и надёжнее, чем передавать сотни float через JSON-массивы
текстом.

ВАЖНО: torch/sentence_transformers импортируются ЛЕНИВО, ТОЛЬКО внутри
обработчиков команд ("load"/"gpu_available"), а не на уровне модуля —
иначе просто ИМПОРТ этого файла (например, случайно откуда-то ещё)
тянул бы за собой ровно ту память, которую весь этот механизм и
пытается изолировать в отдельный процесс.

Не предназначен для прямого запуска пользователем — см. embedding_ipc.
WorkerHandle, который спавнит его через sys.executable (из исходников —
`-m comfyui_studio.promptvault.core.embedding_worker`; из собранного
PyInstaller-exe — тот же самый exe со скрытым CLI-флагом, см.
embedding_ipc.WORKER_CLI_FLAG и диспетчеризацию в корневом main.py).
"""

import base64
import json
import logging
import os
import sys
import traceback

# ВАЖНО: логирование настроено на stderr, НЕ stdout — stdout зарезервирован
# целиком под построчный JSON-протокол (см. _send() ниже), любая
# случайная строка не-JSON на stdout сломала бы парсинг ответа на
# стороне родителя (WorkerHandle._request в embedding_ipc.py читает
# ровно одну строку на один запрос и ожидает валидный JSON).
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] embedding_worker: %(message)s",
)
logger = logging.getLogger(__name__)


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _pick_device(preference: str, torch_module) -> tuple[str, bool]:
    """Портированная копия _pick_device() из embedding.py (см. её
    подробные комментарии про CPU-only сборки torch на Windows и т.п.)
    — переехала сюда, а не осталась там, потому что реально нуждается
    в import torch, а весь смысл выноса в отдельный процесс — не
    делать этого в основном.

    Возвращает (устройство, откатились_ли_с_cuda_на_cpu) — второе
    значение нужно вызывающей стороне (embedding.py) только для того,
    чтобы решить, логировать ли предупреждение пользователю, само
    решение о фактическом устройстве принимается полностью здесь.
    """

    if preference == "cpu":
        return "cpu", False

    try:
        cuda_ok = bool(torch_module.cuda.is_available())
    except Exception:
        cuda_ok = False

    if cuda_ok:
        return "cuda", False

    return "cpu", (preference == "cuda")


def _load_model(
    model_name: str,
    device_preference: str,
    query_prefix: str,
    max_seq_length: int,
):
    """Портированная копия get_model()+_load_and_verify_model() из
    embedding.py (офлайн-сначала логика загрузки, самопроверочный
    encode(), max_seq_length) — см. их комментарии в embedding.py для
    полного объяснения; здесь только сам механизм, без дублирования
    документации."""

    import torch
    from sentence_transformers import SentenceTransformer

    device, fell_back = _pick_device(device_preference, torch)

    previous_offline_flag = os.environ.get("HF_HUB_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "1"
    model = None
    try:
        model = SentenceTransformer(model_name, device=device)
        model.encode(query_prefix + "test", normalize_embeddings=True, show_progress_bar=False)
        logger.info("Модель эмбеддингов загружена офлайн (из локального кэша)")
    except Exception:
        model = None
        logger.info(
            "Модель эмбеддингов не найдена в локальном кэше (или кэш "
            "неполон) — загружаю с сети (huggingface.co)"
        )
    finally:
        if previous_offline_flag is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = previous_offline_flag

    if model is None:
        # офлайн не получилось (кэша ещё нет, самый первый запуск на
        # этой машине, либо кэш повреждён/неполон) — обычная загрузка
        # с сетью; исключения здесь намеренно не ловятся, их обрабатывает
        # общий try/except в run_worker()
        model = SentenceTransformer(model_name, device=device)
        model.encode(query_prefix + "test", normalize_embeddings=True, show_progress_bar=False)

    try:
        model.max_seq_length = max_seq_length
    except Exception:  # noqa: BLE001 — чисто оптимизация, не критично
        pass

    return model, device, fell_back


def run_worker() -> None:
    """Основной цикл: блокирует поток на чтении stdin, пока родитель
    не закроет его (процесс завершился/убил нас) или не пришлёт
    "cmd":"quit". Каждая строка на stdin — один запрос, каждая
    отправленная строка на stdout — один ответ (строго 1:1, без
    конкурентных запросов — см. WorkerHandle на клиентской стороне,
    там один threading.Lock на весь обмен ровно поэтому)."""

    model = None

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except Exception as e:
            _send({"ok": False, "error": f"bad JSON: {e}"})
            continue

        cmd = request.get("cmd")

        try:
            if cmd == "ping":
                _send({"ok": True})

            elif cmd == "quit":
                _send({"ok": True})
                return

            elif cmd == "gpu_available":
                import torch
                try:
                    available = bool(torch.cuda.is_available())
                except Exception:
                    available = False
                _send({"ok": True, "available": available})

            elif cmd == "load":
                model, device, fell_back = _load_model(
                    request["model_name"],
                    request["device_preference"],
                    request.get("query_prefix", ""),
                    request.get("max_seq_length", 256),
                )
                _send({"ok": True, "device": device, "fell_back_to_cpu": fell_back})

            elif cmd == "encode":
                if model is None:
                    _send({"ok": False, "error": "модель не загружена (нет предшествующего 'load')"})
                    continue

                import numpy as np

                vectors = model.encode(
                    request["texts"],
                    normalize_embeddings=True,
                    show_progress_bar=False,
                    batch_size=request.get("batch_size", 32),
                )
                arr = np.asarray(vectors, dtype=np.float32)
                if arr.ndim == 1:
                    arr = arr.reshape(1, -1)

                _send({
                    "ok": True,
                    "shape": list(arr.shape),
                    "data_b64": base64.b64encode(arr.tobytes()).decode("ascii"),
                })

            else:
                _send({"ok": False, "error": f"неизвестная команда воркера: {cmd}"})

        except Exception as e:
            # любая ошибка внутри load/encode (несовместимый torch, нет
            # сети и нет кэша при первом запуске, OOM и т.п.) не должна
            # убивать сам цикл воркера — сбрасываем модель (могла
            # остаться в непонятном состоянии) и отвечаем ошибкой,
            # родитель (embedding_ipc.WorkerHandle) решит, что делать
            # дальше (обычно — показать сообщение и продолжить без
            # семантического поиска, как и раньше делал embedding.py)
            model = None
            _send({"ok": False, "error": str(e), "traceback": traceback.format_exc()})


if __name__ == "__main__":
    run_worker()
