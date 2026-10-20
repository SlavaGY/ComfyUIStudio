"""Local chat UI. This application has no Remote routes or registrations."""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..web_common import NoCacheMiddleware, mount_common_static, register_ui_prefs, static_dir
from .reasoning import ReasoningParser
from .runtime import EngineError, Runtime
from .store import ChatStore


class ChatSettings(BaseModel):
    model: str = Field(default="", max_length=1024)
    system: str = Field(default="", max_length=40000)
    title: str = Field(default="", max_length=200)
    temperature: float = Field(default=0.7, ge=0, le=2)
    top_p: float = Field(default=0.95, gt=0, le=1)
    max_tokens: int = Field(default=2048, ge=1, le=131072)


class SendRequest(ChatSettings):
    text: str = Field(default="", max_length=40000)
    retry: bool = False


class LoadRequest(BaseModel):
    model: str = Field(min_length=1, max_length=1024)
    context_length: int = Field(default=4096, ge=512, le=1048576)


class UnloadRequest(BaseModel):
    instance_id: str = Field(min_length=1, max_length=1024)


def event(data):
    return "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"


def create_app(config: dict, data_dir: Path, runtime=None):
    engine = runtime or Runtime(config)
    store = ChatStore(data_dir / "chats.sqlite3")
    busy = set()
    tasks = {}

    @asynccontextmanager
    async def lifespan(app):
        startup = asyncio.create_task(engine.start())
        try:
            yield
        finally:
            pending = list(tasks.values())
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            # Finish CLI startup before cleanup, so a cancelled to_thread call
            # cannot create an untracked daemon after shutdown.
            await startup
            await engine.close()

    app = FastAPI(title="Studio · LM Studio", lifespan=lifespan)
    app.add_middleware(NoCacheMiddleware)
    app.state.engine = engine
    app.state.store = store
    app.state.generations = tasks

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        # Loopback binding plus host/origin validation prevents an unrelated
        # website from writing chats or triggering model loads on this server.
        host = request.url.hostname
        if host not in ("127.0.0.1", "localhost"):
            return JSONResponse({"detail": "Local application only"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
            return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(EngineError)
    async def engine_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=502)

    def get_chat(chat_id):
        chat = store.get(chat_id)
        if chat is None:
            raise HTTPException(404, "Диалог не найден")
        return chat

    def require_idle(chat_id):
        if chat_id in busy:
            raise HTTPException(409, "Дождитесь завершения ответа или остановите генерацию.")

    def require_ready():
        if engine.state != "ready":
            raise HTTPException(503, engine.error or "Запуск движка LM Studio…")

    @app.get("/api/health")
    def health():
        return {"app": "studio-lm-studio", "state": engine.state, "error": engine.error}

    @app.post("/api/shutdown")
    async def shutdown():
        callback = getattr(app.state, "request_exit", None)
        if callback is not None:
            asyncio.get_running_loop().call_later(0.1, callback)
        return {"ok": True}

    @app.get("/api/models")
    async def models():
        if engine.state == "error":
            await engine.start()
        require_ready()
        return await engine.request("GET", "/api/v1/models", timeout=10)

    @app.post("/api/models/load")
    async def load_model(body: LoadRequest):
        require_ready()
        if busy:
            raise HTTPException(409, "Дождитесь завершения ответа или остановите генерацию.")
        result = await engine.request("POST", "/api/v1/models/load", json=body.model_dump())
        if result.get("instance_id"):
            engine.owned_models.add(result["instance_id"])
        return result

    @app.post("/api/models/unload")
    async def unload_model(body: UnloadRequest):
        require_ready()
        if busy:
            raise HTTPException(409, "Дождитесь завершения ответа или остановите генерацию.")
        result = await engine.request("POST", "/api/v1/models/unload", json=body.model_dump())
        engine.owned_models.discard(body.instance_id)
        return result

    @app.get("/api/chats")
    async def chats():
        return {"chats": store.list()}

    @app.post("/api/chats")
    async def create_chat():
        return store.create()

    @app.get("/api/chats/{chat_id}")
    async def read_chat(chat_id: str):
        return get_chat(chat_id)

    @app.put("/api/chats/{chat_id}")
    async def update_chat(chat_id: str, body: ChatSettings):
        require_idle(chat_id)
        chat = get_chat(chat_id)
        chat.update(body.model_dump())
        store.save(chat)
        return chat

    @app.delete("/api/chats/{chat_id}")
    async def delete_chat(chat_id: str):
        require_idle(chat_id)
        get_chat(chat_id)
        store.delete(chat_id)
        return {"ok": True}

    @app.post("/api/chats/{chat_id}/cancel")
    async def cancel(chat_id: str):
        get_chat(chat_id)
        task = tasks.get(chat_id)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return get_chat(chat_id)

    @app.post("/api/chats/{chat_id}/send")
    async def send(chat_id: str, body: SendRequest):
        require_ready()
        require_idle(chat_id)
        chat = get_chat(chat_id)
        if not body.model.strip():
            raise HTTPException(400, "Выберите модель")
        if not body.retry and not body.text.strip():
            raise HTTPException(400, "Введите сообщение")
        values = body.model_dump(exclude={"text", "retry"})
        if not values["title"]:
            values["title"] = chat["title"]
        chat.update(values)
        messages = chat["messages"]
        if body.retry:
            if messages and messages[-1]["role"] == "assistant":
                messages.pop()
            if not messages or messages[-1]["role"] != "user":
                raise HTTPException(400, "Нет сообщения для повторной генерации")
        else:
            messages.append({"role": "user", "content": body.text.strip()})
        if not chat["title"]:
            chat["title"] = messages[-1]["content"][:70]
        context = [{"role": m["role"], "content": m["content"]} for m in messages]
        if chat["system"]:
            context.insert(0, {"role": "system", "content": chat["system"]})
        payload = {key: chat[key] for key in ("model", "temperature", "top_p", "max_tokens")}
        payload.update(messages=context, stream=True)
        store.save(chat)
        busy.add(chat_id)

        queue = asyncio.Queue()

        async def produce():
            answer = {"role": "assistant", "content": "", "reasoning": "", "status": "interrupted"}
            parser = ReasoningParser()
            started = time.monotonic()
            try:
                async with engine.client.stream("POST", "/v1/chat/completions", json=payload) as response:
                    if not response.is_success:
                        await response.aread()
                        engine.check_response(response)
                    finished = False
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            finished = True
                            break
                        chunk = json.loads(data)
                        if chunk.get("error"):
                            raise EngineError(str(chunk["error"]))
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        content, tagged_reasoning = parser.feed(delta.get("content") or "")
                        reasoning = (delta.get("reasoning_content") or delta.get("reasoning") or "") + tagged_reasoning
                        answer["content"] += content
                        answer["reasoning"] += reasoning
                        queue.put_nowait(event({"content": content, "reasoning": reasoning}))
                    if not finished:
                        raise EngineError("Соединение прервано до завершения ответа.")
                    content, reasoning = parser.feed("", final=True)
                    answer["content"] += content
                    answer["reasoning"] += reasoning
                    if content or reasoning:
                        queue.put_nowait(event({"content": content, "reasoning": reasoning}))
                    answer["status"] = "completed"
                    queue.put_nowait(event({"done": True}))
            except (EngineError, httpx.HTTPError, ValueError) as error:
                answer["status"], answer["error"] = "error", str(error)
                queue.put_nowait(event({"error": str(error)}))
            finally:
                content, reasoning = parser.feed("", final=True)
                answer["content"] += content
                answer["reasoning"] += reasoning
                answer["seconds"] = round(time.monotonic() - started, 2)
                messages.append(answer)
                store.save(chat)
                busy.discard(chat_id)
                tasks.pop(chat_id, None)
                queue.put_nowait(None)

        # Inference owns its cleanup independently of Starlette's response
        # iterator. Closing a browser stream must not leave a suspended async
        # generator holding the partial response and the chat's busy flag.
        task = asyncio.create_task(produce())
        tasks[chat_id] = task

        async def stream():
            try:
                while True:
                    chunk = await queue.get()
                    if chunk is None:
                        break
                    yield chunk
            finally:
                if not task.done():
                    task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    register_ui_prefs(app)
    mount_common_static(app)
    app.mount("/", StaticFiles(directory=str(static_dir("lm_studio")), html=True), name="ui")
    return app
