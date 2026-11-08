"""Shared fixtures: build the host tools once, import the Python template
module from tools/classifier_templates, and point at the fixtures."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
APP = HERE.parent
REPO = APP.parent.parent
HOST = APP / "build" / "host"
TOOLS = REPO / "tools" / "classifier_templates"
STOCK_BUNDLE = REPO / "board" / "tezuka" / "common" / "overlay_tezuka" / "etc" / "classifier-default-templates.bin"


@pytest.fixture(scope="session", autouse=True)
def host_tools():
    subprocess.run(["make", "-s", "-C", str(APP), "host-tools"], check=True)
    return {"scft_check": HOST / "scft_check", "features_check": HOST / "features_check", "jsonw_check": HOST / "jsonw_check"}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def tf():
    return _load("templates_format")


@pytest.fixture(scope="session")
def pyfeatures():
    return _load("features")


@pytest.fixture(scope="session")
def stock_bundle():
    return STOCK_BUNDLE


@pytest.fixture(scope="session")
def frame_fixture():
    return HERE / "fixtures" / "wf_avg4096_frame0.bin"


def run_json(exe, *args):
    """Run a host tool, return (exit code, parsed JSON or None)."""
    p = subprocess.run([str(exe), *map(str, args)], capture_output=True, text=True)
    out = p.stdout.strip()
    try:
        import json
        return p.returncode, json.loads(out) if out else None
    except ValueError:
        return p.returncode, {"_raw": out, "_stderr": p.stderr}
