# -*- coding: utf-8 -*-
"""market_tools.py —— 市场域工具集

**本文件不含任何网络代码。** 所有工具只读本地数据。
这是产品的核心边界：Agent 可以分析，但拿不到新的行情。

数据来源优先级：
  1. 环境变量 `FLOWLIVE_DATA` 指向的数据目录（flowlive-native 的 data/）
  2. 当前工作目录下的 data/
  3. 当前工作目录

面板格式与 flowlive-native 完全一致：
    panel_{tag}.json  ->  {times, codes, names, kinds, source, data_date}
    panel_{tag}.npy   ->  shape (len(times), len(codes))
"""

from __future__ import annotations

import json
import os
import time

import numpy as np

from agent_core.tools import ToolRegistry
from agent_core.permissions import LEVEL_READ, LEVEL_WRITE


def _data_dir() -> str:
    p = os.environ.get("FLOWLIVE_DATA")
    if p and os.path.isdir(p):
        return p
    for cand in ("data", "."):
        if os.path.isdir(cand):
            return cand
    return "."


def _panel_paths(tag: str):
    d = _data_dir()
    return (os.path.join(d, "panel_%s.json" % tag),
            os.path.join(d, "panel_%s.npy" % tag))


def _load_panel(tag: str):
    jp, npth = _panel_paths(tag)
    if not os.path.isfile(jp):
        raise FileNotFoundError(
            "找不到面板 %s（查找目录 %s）。请先导入数据。" % (tag, _data_dir()))
    with open(jp, encoding="utf-8") as f:
        meta = json.load(f)
    mat = None
    if os.path.isfile(npth):
        mat = np.load(npth, allow_pickle=False)
    return meta, mat


# ====================================================================== 工具
registry = ToolRegistry()


@registry.register(
    "_builtin_list_panels",
    "列出本地可用的面板（交易日、分钟数、板块数、来源）",
    required_level=LEVEL_READ,
    params={},
    returns="list[dict]",
)
def list_panels():
    d = _data_dir()
    out = []
    try:
        files = os.listdir(d)
    except OSError:
        return out
    for fn in files:
        if not (fn.startswith("panel_") and fn.endswith(".json")):
            continue
        tag = fn[len("panel_"):-len(".json")]
        try:
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:                      # noqa: BLE001
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


@registry.register(
    "read_panel",
    "读取某个交易日面板的结构信息（时间范围、板块清单、缺失情况）",
    required_level=LEVEL_READ,
    params={"tag": "面板标识，如 20261030"},
    returns="dict",
)
def read_panel(tag: str):
    meta, mat = _load_panel(tag)
    times = meta.get("times") or []
    codes = meta.get("codes") or []
    info = {
        "tag": tag,
        "data_date": meta.get("data_date") or "",
        "source": meta.get("source") or "",
        "minutes": len(times),
        "sectors": len(codes),
        "time_range": [times[0], times[-1]] if times else [],
        "names_sample": dict(list((meta.get("names") or {}).items())[:5]),
    }
    if mat is not None:
        info["shape"] = list(mat.shape)
        info["zero_ratio"] = round(float((mat == 0).mean()), 3)
        info["nan_count"] = int(np.isnan(mat).sum())
    return info


@registry.register(
    "run_salience",
    "对某个面板跑辨识度评分，返回当日最受关注的板块排行",
    required_level=LEVEL_READ,
    params={"tag": "面板标识", "top": "返回前 N 名，默认 10"},
    returns="dict",
)
def run_salience(tag: str, top: int = 10):
    """辨识度评分（简化版）。

    ⚠ 说明：这是 flowlive-native `core/salience.py` 的**简化复刻**，
    用于在没有原工程依赖时也能跑通链路。完整版包含五维合成、
    题材聚类与事件检测，请以原工程为准。

    本版采用三个可解释维度：
      · 体量   V  = 该板块分钟净额的绝对值均值（谁在动）
      · 强度   S  = 净额绝对值 / 全市场该分钟总绝对值（占多大比重）
      · 持续   C  = 非零分钟占比（是一阵还是一直）
    综合分 = 0.45*归一化V + 0.35*S + 0.20*C
    """
    meta, mat = _load_panel(tag)
    if mat is None:
        raise ValueError("面板 %s 缺少 .npy 数据文件" % tag)
    if mat.size == 0:
        raise ValueError("面板 %s 为空" % tag)

    codes = meta.get("codes") or []
    names = meta.get("names") or {}
    mat = np.nan_to_num(mat.astype(float), nan=0.0, posinf=0.0, neginf=0.0)

    v = np.abs(mat).mean(axis=0)                       # 体量
    tot = np.abs(mat).sum(axis=1, keepdims=True)
    tot[tot == 0] = 1.0
    s = (np.abs(mat) / tot).mean(axis=0)               # 强度
    c = (mat != 0).mean(axis=0)                        # 持续

    def norm(x):
        lo, hi = float(x.min()), float(x.max())
        return np.zeros_like(x) if hi <= lo else (x - lo) / (hi - lo)

    score = 0.45 * norm(v) + 0.35 * norm(s) + 0.20 * c
    order = np.argsort(-score)[:max(1, int(top))]

    rows = []
    for i in order:
        rows.append({
            "code": codes[i] if i < len(codes) else "",
            "name": names.get(codes[i], "") if i < len(codes) else "",
            "score": round(float(score[i]), 3),
            "avg_net_yi": round(float(v[i]) / 1e8, 2),
            "share": round(float(s[i]), 4),
            "active_ratio": round(float(c[i]), 3),
        })
    return {"tag": tag, "algorithm": "simplified-salience-v1",
            "top": rows, "universe": len(codes)}


@registry.register(
    "_builtin_salience",
    "对最新一个面板跑辨识度（内部步骤）",
    required_level=LEVEL_READ,
    params={},
    returns="dict",
)
def builtin_salience():
    panels = list_panels()
    if not panels:
        return {"note": "本地没有任何面板，无法计算。请先导入数据。", "top": []}
    tag = panels[0]["tag"]
    return run_salience(tag, top=10)


@registry.register(
    "_builtin_import_hint",
    "给出导入数据的指引（内部步骤）",
    required_level=LEVEL_READ,
    params={},
    returns="str",
)
def builtin_import_hint():
    return (
        "本产品不联网取数。导入方式：\n"
        "1) CSV：需要「时间 / 代码 / 数值」三列，\n"
        "   python -m market_agent.import_cli 你的文件.csv 20261030\n"
        "2) JSON：{\"meta\": {...}, \"values\": [[...]]}\n"
        "3) 也可直接指向 flowlive-native 的 data 目录：\n"
        "   set FLOWLIVE_DATA=D:\\...\\flowlive-native\\build\\core\\data\n"
    )


@registry.register(
    "_builtin_report",
    "汇总成本轮复盘报告（内部步骤）",
    required_level=LEVEL_READ,
    params={},
    returns="dict",
)
def builtin_report():
    panels = list_panels()
    if not panels:
        return "（本地无数据，无法生成报告。请先导入 CSV。）"
    top = panels[0]
    try:
        sal = run_salience(top["tag"], top=5)
    except Exception as e:                     # noqa: BLE001
        return "（辨识度计算失败：%s）" % e

    lines = [
        "# 复盘报告 · %s" % (top.get("data_date") or top["tag"]),
        "",
        "数据来源：%s（本地导入，未联网）" % (top.get("source") or "unknown"),
        "覆盖：%d 分钟 × %d 板块" % (top.get("minutes", 0), top.get("sectors", 0)),
        "",
        "## 辨识度前 5",
    ]
    for i, r in enumerate(sal["top"], 1):
        lines.append("%d. %s（%s） 评分 %.3f · 均净额 %.2f 亿 · 活跃度 %.0f%%"
                     % (i, r["name"] or r["code"], r["code"], r["score"],
                        r["avg_net_yi"], r["active_ratio"] * 100))
    lines += ["", "## 口径说明",
              "辨识度为本产品内置简化算法（体量/强度/持续三维加权），",
              "与 flowlive-native 的完整五维算法不完全一致。"]
    return chr(10).join(lines)


@registry.register(
    "write_report",
    "把复盘报告写入工作区文件",
    required_level=LEVEL_WRITE,
    params={"path": "相对路径，如 reports/20261030.md", "content": "报告正文"},
    returns="dict",
)
def write_report(path: str, content: str):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return {"path": path, "bytes": os.path.getsize(path),
            "written_at": time.strftime("%Y-%m-%d %H:%M:%S")}


@registry.register(
    "query_history",
    "查询某个交易日的面板概要（不读全量矩阵，只看结构）",
    required_level=LEVEL_READ,
    params={"date": "交易日 YYYYMMDD，留空则取最新"},
    returns="dict",
)
def query_history(date: str = ""):
    panels = list_panels()
    if not panels:
        return {"found": False, "note": "本地无数据"}
    if not date:
        return {"found": True, **panels[0]}
    for p in panels:
        if p["data_date"] == date or p["tag"] == date:
            return {"found": True, **p}
    return {"found": False, "note": "未找到 %s" % date,
            "available": [p["data_date"] for p in panels[:10]]}
