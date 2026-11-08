"""tezuka_mcp — host-side, read-only MCP tools for a Tezuka SDR sensor.

Runs on a development/operator machine (never on the Zynq rootfs) and
talks to the sensor over its existing interfaces: the MQTT broker for
the classifier/receiver state and maia-httpd's /waterfall WebSocket and
/api for spectrum snapshots. It never publishes commands, retunes,
transmits, replaces templates or restarts services.
"""

__version__ = "0.1.0"
SCHEMA_SNAPSHOT = "tezuka.mcp.sensor_snapshot/1"
SCHEMA_WATERFALL = "tezuka.mcp.waterfall_snapshot/1"
