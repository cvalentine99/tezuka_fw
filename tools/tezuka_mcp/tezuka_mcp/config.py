"""Configuration from environment variables. Loopback defaults: the server
is meant to run next to an SSH port-forward or on the LAN with the
sensor's address given explicitly; nothing is reached on the open
Internet by default."""
import os
from dataclasses import dataclass


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return default if v is None or v == "" else v


@dataclass(frozen=True)
class Config:
    mqtt_host: str
    mqtt_port: int
    http_host: str
    http_port: int
    waterfall_path: str
    # A live (non-retained) observation older than this is reported stale.
    observation_max_age_s: float
    # A receiver state topic older than this is reported stale (api_controller
    # republishes the whole tree every 2 s, so 10 s means five missed rounds).
    state_max_age_s: float
    # Bounds for snapshot_fpga_waterfall.
    waterfall_max_frames: int
    waterfall_max_timeout_s: float
    # Optional file the server rewrites with a small health JSON document.
    health_path: str
    # Topic prefix of the classifier daemon to follow (its -T option). The
    # default is the production namespace; a test instance started with
    # -T test/classifier/ is followed by setting this to the same prefix.
    classifier_prefix: str

    @staticmethod
    def from_env() -> "Config":
        return Config(
            mqtt_host=_env("TEZUKA_MQTT_HOST", "127.0.0.1"),
            mqtt_port=int(_env("TEZUKA_MQTT_PORT", "1883")),
            http_host=_env("TEZUKA_HTTP_HOST", _env("TEZUKA_MQTT_HOST", "127.0.0.1")),
            http_port=int(_env("TEZUKA_HTTP_PORT", "80")),
            waterfall_path=_env("TEZUKA_WATERFALL_PATH", "/waterfall"),
            observation_max_age_s=float(_env("TEZUKA_OBSERVATION_MAX_AGE_S", "5")),
            state_max_age_s=float(_env("TEZUKA_STATE_MAX_AGE_S", "10")),
            waterfall_max_frames=int(_env("TEZUKA_WATERFALL_MAX_FRAMES", "32")),
            waterfall_max_timeout_s=float(_env("TEZUKA_WATERFALL_MAX_TIMEOUT_S", "60")),
            health_path=_env("TEZUKA_MCP_HEALTH_PATH", ""),
            classifier_prefix=_env("TEZUKA_CLASSIFIER_PREFIX", "state/classifier/"),
        )
