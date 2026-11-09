"""Real MCP handshake over stdio: spawn the server as a subprocess with the
installed SDK's client, list the tools, check the read-only annotations
and call both tools against an unreachable sensor so the honest
"unavailable" paths are exercised end to end."""
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client

PKG_ROOT = Path(__file__).resolve().parent.parent


def _payload(result: types.CallToolResult):
    sc = getattr(result, "structured_content", None) if hasattr(result, "structured_content") else getattr(result, "structuredContent", None)
    if isinstance(sc, dict):
        # The SDK wraps a plain dict return as {"result": {...}}; the text
        # content carries the same document unwrapped.
        return sc["result"] if set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_handshake_list_and_call(tmp_path):
    health = tmp_path / "health.json"
    env = dict(os.environ)
    env.update({"PYTHONPATH": str(PKG_ROOT), "TEZUKA_MQTT_HOST": "127.0.0.1", "TEZUKA_MQTT_PORT": "1",
                "TEZUKA_HTTP_HOST": "127.0.0.1", "TEZUKA_HTTP_PORT": "1", "TEZUKA_MCP_HEALTH_PATH": str(health),
                "TEZUKA_MCP_LOG": "WARNING"})
    params = StdioServerParameters(command=sys.executable, args=["-m", "tezuka_mcp.server"], env=env, cwd=str(PKG_ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            info = getattr(init, "server_info", None) or getattr(init, "serverInfo")
            assert info.name == "tezuka-sensor"
            tools = await session.list_tools()
            names = {t.name: t for t in tools.tools}
            assert set(names) == {"get_sensor_snapshot", "snapshot_fpga_waterfall"}
            for t in names.values():
                a = t.annotations
                assert a is not None
                ro = getattr(a, "read_only_hint", None) if hasattr(a, "read_only_hint") else getattr(a, "readOnlyHint", None)
                de = getattr(a, "destructive_hint", None) if hasattr(a, "destructive_hint") else getattr(a, "destructiveHint", None)
                assert ro is True and de is False

            r = await session.call_tool("get_sensor_snapshot", {})
            assert not (getattr(r, "is_error", None) or getattr(r, "isError", None))
            snap = _payload(r)
            assert snap["schema"].startswith("tezuka.mcp.sensor_snapshot/")
            assert snap["classifier"]["status"] == "unavailable"
            assert snap["receiver"]["rx/frequency"]["value"] is None
            assert snap["provenance"]["subscriber_connected"] is False

            r = await session.call_tool("snapshot_fpga_waterfall", {"max_frames": 1, "timeout_s": 1})
            assert not (getattr(r, "is_error", None) or getattr(r, "isError", None))
            wf = _payload(r)
            assert wf["ok"] is False and "/api failed" in wf["error"]
            assert wf["provenance"]["bounds_applied"]["max_frames"] == 1
    assert health.exists()
    doc = json.loads(health.read_text())
    assert doc["tool_calls"] == 2 and doc["mqtt"]["connected"] is False
