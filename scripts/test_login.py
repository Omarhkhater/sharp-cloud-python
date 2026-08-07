"""Simple Sharp Cloud login test."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(
    0,
    str(
        PROJECT_ROOT
        / "custom_components"
        / "sharp_cocoro_air"
    ),
)

from sharp_cloud import SharpCloud


def main():
    client = SharpCloud()
    client.login()


if __name__ == "__main__":
    main()