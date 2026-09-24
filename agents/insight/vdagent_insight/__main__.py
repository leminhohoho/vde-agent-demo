"""Entrypoint: `python -m <package>` connects this agent to the Backend's hub and serves its turns.

COPIED FROM `agents/_template/` — do not edit in an agent folder.
"""

from .agent import NAME, build_agent
from .host import main

main(NAME, build_agent)
