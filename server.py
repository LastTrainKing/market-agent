#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""server.py —— market-agent 本地 Web 服务

只绑 127.0.0.1，不出网。用标准库 http.server，零第三方依赖。

接口
    GET    /api/state            全部状态（agents / tools / panels / provider）
    POST   /api/agents           新建或更新 Agent
    DELETE /api/agents/<id>      删除自定义 Agent
    POST   /api/run              {agent_id, goal} -> RunResult
    POST   /api/import           {filename, content, tag} -> 导入 CSV 文本
    GET    /api/audit/<id>       最近一次运行的审计记录

用法
    python server.py [--port 8770]
    然后浏览器打开 http://127.0.0.1:8770
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_core.agent import Agent, AgentSpec
from agent_core.audit import AuditLog
from agent_core.builtin import build_builtin_registry, new_custom_agent
from agent_core.permissions import PermissionDenied
from agent_core.providers import make_provider

import localio
import market_tools

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
AGENTS_FILE = "agents.json"
WORKSPACE = os.getcwd()

_registry = None


def registry():
    global _registry
    if _registry is None:
        _registry = build_builtin_registry(path=AGENTS_FILE, workspace=WORKSPACE)
    return _registry


def provider_name():
    return os.environ.get("AGENT_PROVIDER", "offline")


def state():
    reg = registry()
    prov = provider_name()
    key_set = bool(os.environ.get("AGENT_API_KEY"))
    return {
        "agents": [a.as_dict() for a in reg.all()],
        "tools": [
            {"name": n,
             "desc": market_tools.registry.get(n).description,
             "sig": market_tools.registry.get(n).signature_help(),
             "level": market_tools.registry.get(n).required_level}
            for n in market_tools.registry.names()
        ],
        "panels": localio_panels(),
        "provider": {"name": prov, "key_set": key_set,
                     "online": prov != "offline"},
        "shell_mode": localio.mode(),
        "workspace": WORKSPACE,
    }


def localio_panels():
    d = localio.data_dir()
    out = []
    try:
        files = os.listdir(d)
    except OSError:
        return out
    for fn in files:
        if fn.startswith("panel_") and fn.endswith(".json"):
            tag = fn[len("panel_"):-len(".json")]
            try:
                meta = json.load(open(os.path.join(d, fn), encoding="utf-8"))
            except Exception:                       # noqa: BLE001
                continue
            out.append({
                "tag": tag,
                "data_date": meta.get("data_date") or meta.get("date") or "",
                "minutes": len(meta.get("times") or []),
                "sectors": len(meta.get("codes") or []),
                "source": meta.get("source") or "",
            })
    out.sort(key=lambda x: x["data_date"], reverse=True)
    return out


def run_agent(agent_id: str, goal: str):
    reg = registry()
    spec = reg.get(agent_id)
    if spec is None:
        raise ValueError("找不到 Agent：%s" % agent_id)

    prov = make_provider(provider_name(), model=spec.model or "deepseek-chat")
    agent = Agent(spec, market_tools.registry, provider=prov)
    res = agent.run(goal, audit_dir=".audit")
    return {
        "ok": res.ok,
        "answer": res.answer,
        "steps": res.steps,
        "tool_calls": res.tool_calls,
        "denied": res.denied,
        "stats": res.stats,
        "trace": res.trace,
        "audit_path": res.audit_path,
    }


def import_csv_text(filename: str, content: str, tag: str):
    """把浏览器传来的 CSV 文本落盘再走 localio.import_csv。"""
    d = localio.data_dir()
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, "_upload.csv")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    try:
        meta, mat = localio.import_csv(tmp, tag)
        return {"ok": True, "tag": tag, "shape": list(mat.shape),
                "minutes": len(meta["times"]), "sectors": len(meta["codes"])}
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def audit_tail(agent_id: str, n: int = 30):
    d = ".audit"
    if not os.path.isdir(d):
        return []
    files = sorted(f for f in os.listdir(d) if f.startswith("run_%s_" % agent_id))
    if not files:
        return []
    return AuditLog(os.path.join(d, files[-1])).tail(n)


# ====================================================================== HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "market-agent/0.2"

    def log_message(self, fmt, *args):        # 静音默认访问日志
        pass

    # ---------------------------------------------------------------- 工具
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode(),
                   "application/json; charset=utf-8")

    def _err(self, msg: str, code: int = 400):
        self._json({"ok": False, "error": msg}, code)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _static(self, path: str):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(os.path.abspath(WEB_DIR)):
            return self._err("路径越界", 403)
        if not os.path.isfile(full):
            return self._err("未找到 %s" % rel, 404)
        ctype = {".html": "text/html; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8",
                 ".svg": "image/svg+xml"}.get(os.path.splitext(full)[1],
                                              "application/octet-stream")
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    # ---------------------------------------------------------------- 路由
    def do_GET(self):
        p = urlparse(self.path).path
        try:
            if p == "/api/state":
                return self._json(state())
            if p.startswith("/api/audit/"):
                return self._json(audit_tail(unquote(p.rsplit("/", 1)[-1])))
            return self._static(p)
        except Exception as e:                   # noqa: BLE001
            traceback.print_exc()
            return self._err("%s: %s" % (type(e).__name__, e), 500)

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            body = self._body()

            if p == "/api/run":
                return self._json(run_agent(body.get("agent_id", ""),
                                            body.get("goal", "")))

            if p == "/api/import":
                if not localio.import_csv is None and not _import_enabled():
                    return self._err("当前为纯空壳形态，导入已关闭")
                return self._json(import_csv_text(
                    body.get("filename", "upload.csv"),
                    body.get("content", ""),
                    body.get("tag", "")))

            if p == "/api/agents":
                reg = registry()
                aid = (body.get("id") or "").strip()
                if not aid:
                    return self._err("缺少 id")
                if reg.get(aid) is None:
                    new_custom_agent(reg, aid, body.get("name") or aid,
                                     body.get("persona") or "",
                                     level=body.get("level", "read"),
                                     tools=body.get("tools") or [],
                                     provider=body.get("provider", "offline"),
                                     workspace=WORKSPACE)
                else:
                    spec = reg.get(aid)
                    if spec.builtin:
                        return self._err("内置 Agent 不可修改")
                    spec.name = body.get("name", spec.name)
                    spec.persona = body.get("persona", spec.persona)
                    spec.level = body.get("level", spec.level)
                    spec.tools = body.get("tools", spec.tools)
                    spec.max_steps = int(body.get("max_steps", spec.max_steps))
                    reg.save()
                return self._json({"ok": True, "agent": reg.get(aid).as_dict()})

            return self._err("未知接口 %s" % p, 404)

        except PermissionDenied as e:
            return self._err("权限拒绝：%s" % e.reason, 403)
        except Exception as e:                   # noqa: BLE001
            traceback.print_exc()
            return self._err("%s: %s" % (type(e).__name__, e), 500)

    def do_DELETE(self):
        p = urlparse(self.path).path
        if p.startswith("/api/agents/"):
            aid = unquote(p.rsplit("/", 1)[-1])
            ok = registry().remove(aid)
            return self._json({"ok": ok})
        return self._err("未知接口", 404)


def _import_enabled():
    try:
        return localio.import_enabled()
    except Exception:                            # noqa: BLE001
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = "http://127.0.0.1:%d" % args.port
    print("market-agent 已启动：%s" % url)
    print("  工作区   : %s" % WORKSPACE)
    print("  数据目录 : %s" % localio.data_dir())
    print("  Provider : %s%s" % (provider_name(),
          "" if provider_name() == "offline" else "（需 AGENT_API_KEY）"))
    print("  Ctrl+C 停止")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:                        # noqa: BLE001
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
