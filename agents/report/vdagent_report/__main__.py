"""Entrypoint: `python -m <package>` serves this agent over gRPC.

COPIED FROM `agents/_template/` — do not edit in an agent folder.
"""

from .agent import NAME, build_agent
from .host import main

main(NAME, build_agent)
