# -*- coding: utf-8 -*-
"""providers.py —— 模型接入

两类 Provider：

  * `OfflineProvider` —— 确定性规则引擎，**不联网、不需要 Key**。
    用途：让整条链路在无网络、无账号的情况下也能跑通与被测试。
    它不是"降级方案"，而是验收基线：任何 Agent 都应该在离线模式下可复现。

  * `OpenAICompatProvider` —— 兼容 DeepSeek / OpenAI / Ollama 的
    `/chat/completions` 协议。**Key 由用户自填，项目不内置任何 Key。**

⚠ 边界说明（重要）
本产品禁止的是 **行情数据联网抓取**（会触发上游风控、封禁 IP）。
模型 API 调用是另一回事：它由用户自己配置端点与 Key，属于用户自己的资源。
两者在代码里是分开的：`market_tools.py` 不含任何网络代码，
网络只出现在本文件的 Provider 里。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Action:
    """模型的一次输出：要么调工具，要么给结论。"""

    kind: str                       # "tool" | "final"
    thought: str = ""
    tool: str = ""
    args: dict = field(default_factory=dict)
    answer: str = ""

    @staticmethod
    def call(tool: str, args: dict, thought: str = "") -> "Action":
        return Action(kind="tool", tool=tool, args=args, thought=thought)

    @staticmethod
    def final(answer: str, thought: str = "") -> "Action":
        return Action(kind="final", answer=answer, thought=thought)


class Provider:
    name = "base"

    def complete(self, system: str, messages: list[dict],
                 tools_desc: str) -> Action:
        raise NotImplementedError

    def available(self) -> bool:
        return True


# ====================================================================== 离线
class OfflineProvider(Provider):
    """确定性规则引擎：按关键词把目标映射到工具调用。

    设计意图：**没有模型也要能跑**。这让整条链路可测试、可复现，
    也让"Agent 到底做了什么"在审计里一目了然——因为决策是透明的规则，
    不是黑箱。

    规则表按优先级排列，命中即返回。每次调用只走一步，
    由 Agent 循环推进，直到产出结论或触达步数上限。
    """

    name = "offline"

    RULES = [
        # (关键词, 工具, 参数构造, 说明)
        (("有哪些", "列表", "可用", "历史", "面板"), "_builtin_list_panels", {},
         "先看本地有哪些数据"),
        (("导入", "载入", "load"), "_builtin_import_hint", {},
         "给出导入指引"),
        (("辨识度", "主线", "主角", "salience"), "_builtin_salience", {},
         "跑辨识度算法"),
        (("概览", "总结", "复盘", "报告"), "_builtin_report", {},
         "汇总成复盘报告"),
    ]

    def __init__(self):
        self._step = 0

    def complete(self, system: str, messages: list[dict],
                 tools_desc: str) -> Action:
        goal = ""
        for m in messages:
            if m.get("role") == "user":
                goal = m.get("content", "")
        self._step += 1

        done = {m.get("tool") for m in messages if m.get("role") == "tool"}

        # 第一优先：如果还没看过面板列表，先看
        if "_builtin_list_panels" not in done:
            return Action.call("_builtin_list_panels", {},
                               "先确认本地有哪些可用数据")

        # 若目标里提到复盘/报告，且已有面板，则跑辨识度再出报告
        wants_report = any(k in goal for k in ("复盘", "报告", "总结", "概览"))
        if wants_report:
            if "_builtin_salience" not in done:
                return Action.call("_builtin_salience", {},
                                   "先算辨识度，再写复盘")
            if "_builtin_report" not in done:
                return Action.call("_builtin_report", {},
                                   "数据齐了，生成复盘报告")

        # 有报告就用报告正文收尾
        report = ""
        panels = ""
        for m in messages:
            if m.get("role") != "tool":
                continue
            if m.get("tool") == "_builtin_report":
                report = m.get("content", "")
            elif m.get("tool") == "_builtin_list_panels":
                panels = m.get("content", "")

        if report:
            return Action.final(report, "已生成复盘报告")

        parts = ["已完成本轮检查。", "", "本地数据情况：",
                 (panels or "（无）"), "",
                 "如需复盘，请提供导入的 CSV 或说明要看的交易日。"]
        return Action.final(chr(10).join(parts), "无更多可执行步骤")


# ============================================================== OpenAI 兼容
class OpenAICompatProvider(Provider):
    """兼容 /chat/completions 的 Provider。

    Key 从环境变量或构造参数读取，**项目不内置任何 Key**。
    """

    name = "openai-compat"

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str = "deepseek-chat", timeout: int = 60):
        self.base_url = (base_url or os.environ.get("AGENT_BASE_URL")
                         or "https://api.deepseek.com/v1").rstrip("/")
        self.api_key = api_key or os.environ.get("AGENT_API_KEY", "")
        self.model = model or os.environ.get("AGENT_MODEL", "deepseek-chat")
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, system: str, messages: list[dict],
                 tools_desc: str) -> Action:
        if not self.api_key:
            raise RuntimeError(
                "未配置模型 Key。请设置环境变量 AGENT_API_KEY，"
                "或改用离线模式（provider='offline'）。")

        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + [
                {"role": m["role"], "content": m.get("content", "")}
                for m in messages
            ],
            "temperature": 0,
        }
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.api_key},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
        text = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return parse_action(text)


# ---------------------------------------------------------------- 解析
_TOOL_RE = re.compile(
    r'```(?:json)?\s*(\{.*?\})\s*```|(\{[^{}]*"tool"\s*:[^{}]*\})', re.S)


def parse_action(text: str) -> Action:
    """从模型输出里提取 Action。

    约定：模型要么输出一个 `{"tool": ..., "args": {...}}` 的 JSON 块，
    要么直接输出自然语言结论。
    """
    m = _TOOL_RE.search(text or "")
    if m:
        raw = m.group(1) or m.group(2)
        try:
            j = json.loads(raw)
            if "tool" in j:
                return Action.call(str(j["tool"]), j.get("args") or {},
                                   thought=j.get("thought", ""))
        except json.JSONDecodeError:
            pass
    return Action.final((text or "").strip())


def make_provider(name: str = "offline", **kw) -> Provider:
    if name in ("offline", "", None):
        return OfflineProvider()
    if name in ("openai", "openai-compat", "deepseek", "ollama"):
        return OpenAICompatProvider(**kw)
    raise ValueError("未知 provider：%s" % name)
