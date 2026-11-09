"""Optional: run the cross-compiled daemon's -V validate mode under
qemu-arm-static. Skipped unless the ARM binary, qemu and the sysroot are
present (set TEZUKA_ARM_SYSROOT to the Buildroot target sysroot).

The ARM binary is built by `make classifier` with the Buildroot SDK's
CROSS_COMPILE and placed at build/arm/classifier for this test."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import APP

ARM_BIN = APP / "build" / "arm" / "classifier"
SYSROOT = os.environ.get("TEZUKA_ARM_SYSROOT", "")
QEMU = shutil.which("qemu-arm-static")

pytestmark = pytest.mark.skipif(not (ARM_BIN.exists() and QEMU and SYSROOT and Path(SYSROOT).is_dir()),
                                reason="ARM binary, qemu-arm-static or TEZUKA_ARM_SYSROOT not available")


def run_v(path):
    p = subprocess.run([QEMU, "-L", SYSROOT, str(ARM_BIN), "-V", str(path)], capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip())


def test_arm_validate_stock_bundle(stock_bundle, host_tools):
    rc, rep = run_v(stock_bundle)
    assert rc == 0 and rep["valid"] and rep["count"] == 3 and rep["labels"] == ["FM Broadcast", "DAB+", "CW / Beacon"]
    # the hash must match the host parser's
    host = json.loads(subprocess.run([str(host_tools["scft_check"]), str(stock_bundle)], capture_output=True, text=True).stdout)
    assert rep["sha256"] == host["sha256"]


def test_arm_validate_rejects_truncated(tmp_path, stock_bundle):
    p = tmp_path / "t.bin"
    p.write_bytes(stock_bundle.read_bytes()[:3000])
    rc, rep = run_v(p)
    assert rc == 2 and rep["valid"] is False and rep["status"] == "bad_size"
