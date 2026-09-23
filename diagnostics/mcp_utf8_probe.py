"""End-to-end probe for the aic-leaderboard MCP stdio encoding bug.

Spawns the MCP server exactly the way the host would (host-style env: no
PYTHONUTF8 / PYTHONIOENCODING injected), speaks raw JSON-RPC over stdio using
UTF-8 bytes, and reports whether a *Chinese* candidate path survives the trip.

Usage:
    python mcp_utf8_probe.py            # both arms (as-registered  vs  with -X utf8)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PY = r"D:\04_Tools\Python\python.exe"
ROOT = r"D:\02_Projects\ML\jinyinsai1\auto_review"

CHINESE_ZIP = r"D:\02_Projects\ML\jinyinsai1_nolimit\submission_ours_94\复赛结果_鱼不吃猫.zip"
ASCII_ZIP = r"D:\02_Projects\ML\jinyinsai1_nolimit\runs\_cand_ascii.zip"

BASE_ENV = {
    k: v
    for k, v in os.environ.items()
    if k.upper() not in ("PYTHONUTF8", "PYTHONIOENCODING", "PYTHONLEGACYWINDOWSSTDIO")
}
BASE_ENV["AIC_LEADERBOARD_ROOT"] = ROOT


def rpc(child, msg: dict, escape: bool = True) -> dict | None:
    raw = json.dumps(msg, ensure_ascii=escape)
    child.stdin.write((raw + "\n").encode("utf-8"))
    child.stdin.flush()
    while True:
        line = child.stdout.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line.decode("utf-8", "replace"))
        except Exception:
            continue
        if obj.get("id") == msg.get("id"):
            return obj


def arm(label: str, args: list[str], target: str, escape: bool = True) -> None:
    env = dict(BASE_ENV)
    child = subprocess.Popen(
        [PY, *args],
        cwd=ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        rpc(child, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "probe", "version": "1"}},
        }, escape=escape)
        child.stdin.write(
            (json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            .encode("utf-8")
        )
        child.stdin.flush()

        stem = Path(target).stem
        resp = rpc(child, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "aic_validate_candidate",
                       "arguments": {"path": target, "stage": "semi"}},
        }, escape=escape)
    finally:
        try:
            child.stdin.close()
        except Exception:
            pass
        try:
            child.wait(timeout=10)
        except Exception:
            child.kill()

    if resp is None:
        print(f"  [{label}] <no response>")
        return
    if "error" in resp:
        print(f"  [{label}] ERROR: {resp['error'].get('message')}")
        return
    content = resp.get("result", {}).get("content") or []
    text = content[0].get("text") if content else json.dumps(resp.get("result"))[:200]
    try:
        payload = json.loads(text)
    except Exception:
        print(f"  [{label}] OK (raw) {text[:120]}")
        return
    sha = str(payload.get("sha256", ""))[:16]
    print(f"  [{label}] OK sha256={sha} bytes={payload.get('bytes')} entries={payload.get('entries')}")
    echoed = str(payload.get("path", ""))
    clean = stem in echoed
    print(f"  [{label}] path echo: {'clean' if clean else 'MOJIBAKE'}  <{echoed}>")


def main() -> int:
    args_as_registered = ["-m", "aic_leaderboard.mcp"]
    args_fixed = ["-X", "utf8", "-m", "aic_leaderboard.mcp"]

    print("== leg 1: client sends ASCII-escaped (\\uXXXX) JSON ==")
    print("arm A -- as originally registered (no -X utf8)")
    arm("chinese", args_as_registered, CHINESE_ZIP)
    arm("ascii  ", args_as_registered, ASCII_ZIP)
    print("arm B -- with -X utf8 (the fix)")
    arm("chinese", args_fixed, CHINESE_ZIP)
    arm("ascii  ", args_fixed, ASCII_ZIP)

    print()
    print("== leg 2: client sends raw UTF-8 JSON (ensure_ascii=False) ==")
    print("arm A -- as originally registered (no -X utf8)")
    arm("chinese", args_as_registered, CHINESE_ZIP, escape=False)
    arm("ascii  ", args_as_registered, ASCII_ZIP, escape=False)
    print("arm B -- with -X utf8 (the fix)")
    arm("chinese", args_fixed, CHINESE_ZIP, escape=False)
    arm("ascii  ", args_fixed, ASCII_ZIP, escape=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
