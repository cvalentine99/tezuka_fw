"""Call one tezuka-sensor MCP tool through a real stdio MCP client and print the structured result."""
import asyncio, json, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main(tool, args):
    p = StdioServerParameters(command="/home/cvalentine/.venvs/tezuka-mcp/bin/tezuka-mcp", args=[],
                              env={"TEZUKA_MQTT_HOST": "192.168.50.25", "TEZUKA_HTTP_HOST": "192.168.50.25"})
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            await asyncio.sleep(3.0)  # let the MQTT cache fill with live messages
            res = await s.call_tool(tool, args)
            sc = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
            print(json.dumps(sc.get("result", sc) if isinstance(sc, dict) else sc))

asyncio.run(main(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}))
