"""启动 Agent Eval 后端服务。

用法（在 backend/ 目录下）：
    python run_server.py [--host 127.0.0.1] [--port 8000] [--reload]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_ROOT / "src"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Start Agent Eval backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code change")
    args = parser.parse_args()

    os.chdir(BACKEND_ROOT)
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
