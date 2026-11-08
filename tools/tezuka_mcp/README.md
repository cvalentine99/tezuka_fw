# tezuka-mcp — read-only MCP tools for a Tezuka SDR sensor

Runs on a host machine (never on the Zynq rootfs) and exposes two
read-only tools over MCP stdio:

* `get_sensor_snapshot` — cached receiver + classifier state from a
  persistent MQTT subscription, with provenance and per-field freshness.
* `snapshot_fpga_waterfall` — a bounded collection of maia-httpd
  `/waterfall` frames, checked for one coherent context and summarized
  (grid, PSD summary, declared noise-floor estimate, above-floor segments).

Neither tool publishes commands, retunes, transmits, replaces templates or
restarts anything. There is deliberately no generic MQTT command tool.
The full contract (units, grid, freshness rules, schemas) is in
[`docs/classifier_telemetry.md`](../../docs/classifier_telemetry.md).

## Install

```bash
python3 -m venv ~/.venvs/tezuka-mcp
~/.venvs/tezuka-mcp/bin/pip install -e 'tools/tezuka_mcp[test]'
```

Requires the MCP Python SDK 2.x (`mcp>=2`), `paho-mqtt>=2` and
`websockets>=12`.

## Run

```bash
TEZUKA_MQTT_HOST=192.168.50.25 TEZUKA_HTTP_HOST=192.168.50.25 ~/.venvs/tezuka-mcp/bin/tezuka-mcp
```

Diagnostics go to stderr; stdout is the MCP protocol. Configuration is by
environment variable (defaults are loopback, for use next to an SSH port
forward):

| Variable | Default | Meaning |
|---|---|---|
| `TEZUKA_MQTT_HOST` / `TEZUKA_MQTT_PORT` | `127.0.0.1` / `1883` | the sensor's broker |
| `TEZUKA_HTTP_HOST` / `TEZUKA_HTTP_PORT` | MQTT host / `80` | maia-httpd (`/api`, `/waterfall`) |
| `TEZUKA_WATERFALL_PATH` | `/waterfall` | |
| `TEZUKA_CLASSIFIER_PREFIX` | `state/classifier/` | the daemon's `-T` prefix to follow (e.g. `test/classifier/` for a side-by-side test instance) |
| `TEZUKA_OBSERVATION_MAX_AGE_S` | `5` | a live observation older than this is `stale` |
| `TEZUKA_STATE_MAX_AGE_S` | `10` | same for `state/rx/*` and `state/main/*` |
| `TEZUKA_WATERFALL_MAX_FRAMES` / `TEZUKA_WATERFALL_MAX_TIMEOUT_S` | `32` / `60` | hard bounds on a waterfall request |
| `TEZUKA_MCP_HEALTH_PATH` | unset | if set, a small JSON health document is rewritten atomically on start and after every tool call |
| `TEZUKA_MCP_LOG` | `INFO` | stderr log level |

Claude Desktop / Claude Code style client entry:

```json
{
  "mcpServers": {
    "tezuka-sensor": {
      "command": "/home/you/.venvs/tezuka-mcp/bin/tezuka-mcp",
      "env": { "TEZUKA_MQTT_HOST": "192.168.50.25", "TEZUKA_HTTP_HOST": "192.168.50.25" }
    }
  }
}
```

The SDK returns a plain-dict tool result as `structuredContent: {"result":
{...}}`; the text content carries the same JSON document unwrapped.

## Reading the results honestly

* `status` per field: `fresh`, `stale`, `retained` (only the broker's
  replayed copy was seen since this server connected — the producer may be
  down) or `unavailable`. Retained replays never count as fresh, and a
  repeated observation `seq` is ignored.
* `classifier.best_effort: true` means the sensor runs a daemon without the
  `observation` message; sequence, boot id, template hash and feature
  semantics are then `null`, not guessed.
* Waterfall levels are dB relative to the producer's power scale, never
  dBm. DDC input, sweep mode and a changed context are reported as
  unsupported/rejected instead of being averaged.
* On the deployed firmware the frame's step word free-runs 0..7 even with
  sweep off; the tool uses the MQTT sweep state to tell that apart from
  real stepping and rejects the collection when that state is unknown.

## Tests

```bash
cd tools/tezuka_mcp && ~/.venvs/tezuka-mcp/bin/python -m pytest -q
```

Covers the frame contract on recorded frames (`tests/fixtures`, with the
producer's `/api` document), the freshness rules, the summary and
coherence checks, an end-to-end collection against a local fake sensor,
and a real stdio handshake with the installed SDK (tool listing,
annotations, both tools against an unreachable sensor).
