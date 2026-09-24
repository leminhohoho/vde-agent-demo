"""Generate Python gRPC stubs for proto/agent.proto into proto/vdagent_proto/.

Usage: uv run python proto/scripts/gen.py
"""

import sys
from importlib.resources import files
from pathlib import Path

from grpc_tools import protoc

PROTO_DIR = Path(__file__).resolve().parent.parent


def main() -> int:
    well_known = str(files("grpc_tools") / "_proto")
    # Map proto/ to the virtual import path `vdagent_proto/` so generated code uses
    # `from vdagent_proto import agent_pb2` instead of a bare top-level import.
    args = [
        "grpc_tools.protoc",
        f"-I{well_known}",
        f"-Ivdagent_proto={PROTO_DIR}",
        f"--python_out={PROTO_DIR}",
        f"--pyi_out={PROTO_DIR}",
        f"--grpc_python_out={PROTO_DIR}",
        "vdagent_proto/agent.proto",
    ]
    rc = protoc.main(args)
    if rc != 0:
        print("protoc failed", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
