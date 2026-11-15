"""The version is stated twice, in pyproject.toml and in ``__version__``; they must agree."""

from importlib.metadata import version

import grundfos_ble


def test_version_matches_the_package_metadata() -> None:
    assert grundfos_ble.__version__ == version("grundfos-ble")
