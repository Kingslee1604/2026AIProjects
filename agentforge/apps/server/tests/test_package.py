import re

import agentforge


def test_package_exposes_semver_version() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", agentforge.__version__)
