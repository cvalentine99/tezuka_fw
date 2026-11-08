import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent
REPO_ROOT = PKG_ROOT.parent.parent
FIXTURES = HERE / "fixtures"

sys.path.insert(0, str(PKG_ROOT))


@pytest.fixture(scope="session")
def fixture_frames():
    frames = []
    for i in range(3):
        frames.append((FIXTURES / f"wf_avg4096_frame{i}.bin").read_bytes())
    return frames


@pytest.fixture(scope="session")
def fixture_meta():
    return json.loads((FIXTURES / "wf_avg4096_meta.json").read_text())


@pytest.fixture(scope="session")
def repo_root():
    return REPO_ROOT
