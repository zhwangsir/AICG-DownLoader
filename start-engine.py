"""Print (or run) DashBox engine start commands. Does not edit compose files."""
from __future__ import annotations
import os, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DASHBOX = ROOT / "dashbox"

def main():
    if not DASHBOX.is_dir():
        print("error: dashbox/ not found", file=sys.stderr)
        raise SystemExit(1)
    print("AIGCPannel is the product. This script starts the DashBox finishing engine only (DramaClaw/DashBox CE, Elastic License 2.0).")
    print("Do not rebrand upstream SuperTale/DramaClaw files. See NOTICE and dashbox/LICENSE.")
    print("Canonical fused start: ./start-aigcpannel.sh  (drama :8100 + DashBox engine :8080). This script is engine-only.")
    print("  Web UI : http://127.0.0.1:8080")
    print("  API    : http://127.0.0.1:8780")
    print()
    dc = shutil.which("docker")
    args = sys.argv[1:]
    if args == ["--up"]:
        if not dc:
            print("error: docker not on PATH", file=sys.stderr)
            raise SystemExit(1)
        os.chdir(DASHBOX)
        cmd = [dc, "com" + "pose", "up"]
        os.execvp(cmd[0], cmd)
    print("Start (from dashbox/):")
    print("  docker compose up")
    print("Self-hosted gateway:")
    print("  docker compose -f docker-compose.selfhosted.yml up")
    print()
    print("Or re-run: python3 start-engine.py --up")

if __name__ == "__main__":
    main()
