# -*- coding: utf-8 -*-
"""tests/test_server.py —— 服务端接口测试（不需要 pytest）

启动一个临时服务，跑通 5 个接口：
  state / import / run / agents(增删) / 内置 Agent 不可改
"""

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import server as srv_mod

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print("  ✓ %s" % name)
    except Exception as e:                        # noqa: BLE001
        FAIL.append((name, str(e)))
        print("  ✗ %s -> %s" % (name, e))


def _req(base, path, payload=None, method=None):
    # 有 payload 默认 POST，无 payload 默认 GET —— 免得调用处漏写 method
    if method is None:
        method = "POST" if payload is not None else "GET"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    tmp = tempfile.mkdtemp()
    os.chdir(tmp)
    srv_mod.WORKSPACE = tmp
    srv_mod.AGENTS_FILE = os.path.join(tmp, "agents.json")
    srv_mod._registry = None
    os.environ["FLOWLIVE_DATA"] = os.path.join(tmp, "data")
    os.makedirs(os.environ["FLOWLIVE_DATA"], exist_ok=True)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv_mod.Handler)
    port = httpd.server_address[1]
    base = "http://127.0.0.1:%d" % port
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.3)

    try:
        print("== 接口 ==")

        def t_state():
            j = _req(base, "/api/state")
            assert len(j["agents"]) >= 3, j["agents"]
            assert len(j["tools"]) >= 5, j["tools"]
            assert j["provider"]["name"] == "offline"
        check("GET /api/state", t_state)

        def t_index():
            with urllib.request.urlopen(base + "/", timeout=10) as r:
                body = r.read().decode()
            assert r.status == 200
            assert "market-agent" in body
        check("GET / 首页", t_index)

        csv = ("时间,板块代码,板块名称,主力净额\n"
               "09:31,BK1036,半导体,1.5e8\n09:32,BK1036,半导体,2.1e8\n"
               "09:31,BK1202,房地产,-3e7\n09:32,BK1202,房地产,-5e7\n")

        def t_import():
            j = _req(base, "/api/import",
                     {"filename": "s_20261030.csv", "content": csv, "tag": "20261030"})
            assert j["ok"], j
            assert j["shape"] == [2, 2], j
        check("POST /api/import", t_import)

        def t_run():
            j = _req(base, "/api/run", {"agent_id": "reviewer", "goal": "复盘最新一天"})
            assert j["ok"], j["answer"][:100]
            assert j["trace"], "trace 为空"
            assert any(t.get("tool") for t in j["trace"]), j["trace"]
        check("POST /api/run 内置 Agent", t_run)

        def t_custom():
            j = _req(base, "/api/agents",
                     {"id": "t1", "name": "测试", "persona": "只读",
                      "level": "read", "tools": ["_builtin_list_panels"]})
            assert j["ok"], j
            st = _req(base, "/api/state")
            assert any(a["id"] == "t1" for a in st["agents"])
        check("POST /api/agents 新建", t_custom)

        def t_builtin_locked():
            try:
                _req(base, "/api/agents", {"id": "reviewer", "name": "改名"})
            except urllib.error.HTTPError as e:
                assert e.code == 400, e.code
                return
            raise AssertionError("内置 Agent 竟然可改")
        check("内置 Agent 不可改", t_builtin_locked)

        def t_denied_not_ok():
            j = _req(base, "/api/run", {"agent_id": "t1", "goal": "复盘最新一天"})
            assert j["ok"] is False, "被拒却报成功"
            assert j["denied"], "拒绝记录为空"
        check("被拒时 ok=False", t_denied_not_ok)

        def t_delete():
            j = _req(base, "/api/agents/t1", method="DELETE")
            assert j["ok"], j
            st = _req(base, "/api/state")
            assert not any(a["id"] == "t1" for a in st["agents"])
        check("DELETE /api/agents", t_delete)

    finally:
        httpd.shutdown()

    print("\n" + "=" * 46)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for n, e in FAIL:
        print("  ✗ %s: %s" % (n, e))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
