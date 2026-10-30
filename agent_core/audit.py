# -*- coding: utf-8 -*-
"""audit.py —— 审计日志

每一次工具调用、每一次权限拒绝、每一个最终交付物，都落一条 JSONL。
目的只有一个：**任何一次结论都能回溯到它的输入证据**。
"""

from __future__ import annotations

import json
import os
import time


class AuditLog:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self._n = 0

    # ---------------------------------------------------------------- 写入
    def _write(self, rec: dict):
        rec["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        rec["seq"] = self._n
        self._n += 1
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def run_start(self, agent: str, goal: str, perm: dict):
        self._write({"kind": "run_start", "agent": agent, "goal": goal,
                     "permission": perm})

    def step(self, n: int, thought: str, tool: str = "", args: dict | None = None):
        self._write({"kind": "step", "n": n, "thought": thought,
                     "tool": tool, "args": args or {}})

    def tool_call(self, name: str, args: dict, result):
        self._write({
            "kind": "tool_call", "tool": name, "args": args,
            "ok": getattr(result, "ok", False),
            "error": getattr(result, "error", ""),
            "duration_ms": getattr(result, "duration_ms", 0),
            "data_preview": str(getattr(result, "data", ""))[:300],
        })

    def denied(self, reason: str, rule: str = ""):
        self._write({"kind": "denied", "reason": reason, "rule": rule})

    def deliverable(self, path: str, verified: bool, note: str = ""):
        self._write({"kind": "deliverable", "path": path,
                     "verified": verified, "note": note})

    def run_end(self, status: str, summary: str, stats: dict):
        self._write({"kind": "run_end", "status": status,
                     "summary": summary, "stats": stats})

    # ---------------------------------------------------------------- 读取
    def tail(self, n: int = 20) -> list[dict]:
        try:
            with open(self.path, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return []
        out = []
        for line in lines[-n:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
