# -*- coding: utf-8 -*-
"""tests/test_core.py —— 核心链路测试

不需要 pytest，直接 `python tests/test_core.py` 即可运行。
覆盖四条必须成立的规则：
  1. 权限闸门挡住越界与越级
  2. 联网开关无法打开
  3. 无效重试被拒绝
  4. 离线模式下整条链路可跑通并留下审计
"""

import os
import sys
import tempfile
import json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import numpy as np

from agent_core.permissions import (Permission, PermissionGate, PermissionDenied,
                                    LEVEL_READ, LEVEL_WRITE, standard, strict_readonly)
from agent_core.tools import ToolRegistry, ToolResult
from agent_core.agent import Agent, AgentSpec
from agent_core.providers import OfflineProvider, Action, parse_action
from agent_core.builtin import build_builtin_registry

import market_tools
import localio


PASS = []
FAIL = []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print("  ✓ %s" % name)
    except AssertionError as e:
        FAIL.append((name, str(e)))
        print("  ✗ %s  -> %s" % (name, e))
    except Exception as e:                        # noqa: BLE001
        FAIL.append((name, "%s: %s" % (type(e).__name__, e)))
        print("  ✗ %s  -> %s: %s" % (name, type(e).__name__, e))


# ---------------------------------------------------------------- 夹具
def make_panel(tmpdir, tag="20261030"):
    """造一份 2 分钟 × 3 板块的面板。"""
    csv_path = os.path.join(tmpdir, "sample_%s.csv" % tag)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        f.write("时间,板块代码,板块名称,主力净额\n")
        f.write("09:31,BK1036,半导体,150000000\n")
        f.write("09:32,BK1036,半导体,210000000\n")
        f.write("09:31,BK1202,房地产,-30000000\n")
        f.write("09:32,BK1202,房地产,-50000000\n")
        f.write("09:31,BK0801,白酒,8000000\n")
        f.write("09:32,BK0801,白酒,12000000\n")
    return localio.import_csv(csv_path, tag, out_dir=tmpdir)


# ---------------------------------------------------------------- 用例
def test_gate_blocks_network():
    try:
        Permission(network=True)
        PermissionGate(Permission(network=True))
    except ValueError as e:
        assert "禁止" in str(e) or "network" in str(e), str(e)
        return
    raise AssertionError("network=True 竟然被接受了")


def test_gate_level():
    g = PermissionGate(strict_readonly("."))
    try:
        g.check("write_report", LEVEL_WRITE, {"path": "a.md"})
    except PermissionDenied as e:
        assert "权限" in e.reason or "等级" in e.reason, e.reason
        return
    raise AssertionError("只读配置竟然放行了写操作")


def test_gate_sandbox():
    tmp = tempfile.mkdtemp()
    g = PermissionGate(standard(tmp))
    try:
        g.resolve_path("../../etc/passwd")
    except PermissionDenied as e:
        assert "越界" in e.reason, e.reason
        return
    raise AssertionError("越界路径竟然通过了")


def test_gate_allowlist():
    tmp = tempfile.mkdtemp()
    p = Permission(level=LEVEL_WRITE, workspace=tmp, allow_tools=("read_panel",))
    g = PermissionGate(p)
    try:
        g.check("write_report", LEVEL_READ, {})
    except PermissionDenied as e:
        assert "允许列表" in e.reason, e.reason
        return
    raise AssertionError("白名单外的工具竟然放行了")


def test_import_and_salience():
    tmp = tempfile.mkdtemp()
    meta, mat = make_panel(tmp)
    assert mat.shape == (2, 3), mat.shape
    assert len(meta["names"]) == 3, meta["names"]

    os.environ["FLOWLIVE_DATA"] = tmp
    try:
        panels = market_tools.list_panels()
        assert panels and panels[0]["tag"] == "20261030", panels
        sal = market_tools.run_salience("20261030", top=3)
        assert sal["universe"] == 3, sal
        assert sal["top"][0]["code"] == "90.BK1036", sal["top"]
        assert sal["top"][0]["score"] >= sal["top"][-1]["score"]
    finally:
        os.environ.pop("FLOWLIVE_DATA", None)


def test_tool_permission_denied_is_recorded():
    tmp = tempfile.mkdtemp()
    make_panel(tmp)
    os.environ["FLOWLIVE_DATA"] = tmp
    try:
        spec = AgentSpec(id="t", name="t", persona="p", level="read",
                         workspace=tmp, tools=["write_report"])
        agent = Agent(spec, market_tools.registry, provider=OfflineProvider())
        res = agent.run("写个报告", audit_dir=os.path.join(tmp, ".audit"))
        assert res.ok is False or res.denied, res.brief()
    finally:
        os.environ.pop("FLOWLIVE_DATA", None)


def test_offline_agent_runs_end_to_end():
    tmp = tempfile.mkdtemp()
    make_panel(tmp)
    os.environ["FLOWLIVE_DATA"] = tmp
    try:
        reg = build_builtin_registry(path=os.path.join(tmp, "agents.json"),
                                     workspace=tmp)
        spec = reg.get("reviewer")
        assert spec is not None
        agent = Agent(spec, market_tools.registry, provider=OfflineProvider())
        res = agent.run("复盘最新一天", audit_dir=os.path.join(tmp, ".audit"))
        assert res.ok, res.brief() + " | " + res.answer[:200]
        assert len(res.tool_calls) >= 2, res.tool_calls
        assert os.path.isfile(res.audit_path), res.audit_path
    finally:
        os.environ.pop("FLOWLIVE_DATA", None)


def test_identical_retry_refused():
    """连续两次相同工具+参数，第二次必须被拒。"""
    tmp = tempfile.mkdtemp()
    make_panel(tmp)
    os.environ["FLOWLIVE_DATA"] = tmp
    try:
        class Stubborn(OfflineProvider):
            def complete(self, system, messages, tools_desc):
                # 永远请求同一个工具同一组参数
                return Action.call("_builtin_list_panels", {}, "重复")

        spec = AgentSpec(id="s", name="s", persona="p", level="read",
                         workspace=tmp, max_steps=4)
        agent = Agent(spec, market_tools.registry, provider=Stubborn())
        res = agent.run("测试重试", audit_dir=os.path.join(tmp, ".audit"))
        assert any("无效重试" in d for d in res.denied), res.denied
    finally:
        os.environ.pop("FLOWLIVE_DATA", None)


def test_parse_action():
    a = parse_action('```json\n{"tool":"read_panel","args":{"tag":"x"}}\n```')
    assert a.kind == "tool" and a.tool == "read_panel" and a.args["tag"] == "x"
    b = parse_action("今天就这些，没什么特别的。")
    assert b.kind == "final" and "没什么特别" in b.answer


def test_custom_agent_persists():
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "agents.json")
    reg = build_builtin_registry(path=path, workspace=tmp)
    from agent_core.builtin import new_custom_agent
    new_custom_agent(reg, "mine", "我的助手", "只读数据", level="read", workspace=tmp)
    reg2 = build_builtin_registry(path=path, workspace=tmp)
    assert reg2.get("mine") is not None, "自定义 Agent 未持久化"
    assert reg2.get("reviewer") is not None, "内置 Agent 丢失"


def main():
    print("== 权限 ==")
    check("联网开关无法打开", test_gate_blocks_network)
    check("等级不足被拒", test_gate_level)
    check("路径越界被拒", test_gate_sandbox)
    check("白名单外被拒", test_gate_allowlist)

    print("\n== 数据与工具 ==")
    check("CSV 导入 + 辨识度", test_import_and_salience)
    check("越权调用被记录", test_tool_permission_denied_is_recorded)

    print("\n== Agent 循环 ==")
    check("离线端到端跑通", test_offline_agent_runs_end_to_end)
    check("无效重试被拒", test_identical_retry_refused)
    check("模型输出解析", test_parse_action)
    check("自定义 Agent 持久化", test_custom_agent_persists)

    print("\n" + "=" * 50)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for n, e in FAIL:
        print("  ✗ %s: %s" % (n, e))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
