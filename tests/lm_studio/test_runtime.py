"""Headless startup ownership and CLI progress handling."""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from comfyui_studio.lm_studio.runtime import EngineError, Runtime, cli_status


def test_cli_progress_before_machine_readable_status():
    assert cli_status('Starting LM Studio...\n{"status":"running","pid":42}\n')["pid"] == 42
    with pytest.raises(EngineError):
        cli_status("not JSON")


def test_existing_server_is_not_started_or_stopped():
    async def scenario():
        client = httpx.AsyncClient(base_url="http://127.0.0.1", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"models": []})))
        runtime = Runtime({}, client)
        runtime.command = AsyncMock()
        await runtime.start()
        assert runtime.state == "ready"
        await runtime.close()
        runtime.command.assert_not_called()
    asyncio.run(scenario())


def test_authentication_failure_does_not_restart_runtime():
    async def scenario():
        client = httpx.AsyncClient(base_url="http://127.0.0.1", transport=httpx.MockTransport(lambda r: httpx.Response(401, json={})))
        runtime = Runtime({}, client)
        runtime.command = AsyncMock()
        await runtime.start()
        assert runtime.state == "error" and "токен" in runtime.error
        await runtime.close()
        runtime.command.assert_not_called()
    asyncio.run(scenario())


def test_created_daemon_and_server_are_released_but_only_matching_pid():
    async def scenario():
        runtime = Runtime({})
        runtime.request = AsyncMock()
        offline = EngineError("offline")
        offline.__cause__ = httpx.ConnectError("offline")
        runtime.request.side_effect = [offline, {"models": []}]
        runtime.command = AsyncMock(side_effect=[
            '{"status":"not-running"}',
            'Starting...\n{"status":"running","pid":99,"isDaemon":true}',
            'started', 'stopped', '{"status":"running","pid":99,"isDaemon":true}', 'down',
        ])
        await runtime.start()
        assert runtime.state == "ready" and runtime.owns_server and runtime.daemon_pid == 99
        await runtime.close()
        assert runtime.command.call_args_list[-1].args == ("daemon", "down")
    asyncio.run(scenario())


def test_replaced_daemon_is_left_running():
    async def scenario():
        runtime = Runtime({})
        runtime.daemon_pid = 99
        runtime.command = AsyncMock(return_value='{"status":"running","pid":100,"isDaemon":true}')
        await runtime.close()
        runtime.command.assert_awaited_once_with("daemon", "status", "--json")
    asyncio.run(scenario())
