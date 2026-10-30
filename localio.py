# -*- coding: utf-8 -*-
"""localio.py —— 本地数据导入（market-agent 自带，不依赖 flowlive-native）

与 flowlive-native 的 `core/localio.py` 面板格式完全一致，
因此两边数据可以互通：在这里导入的数据，flowlive 的 Shell 版也能直接回放。

本文件不含任何网络代码。
"""

from __future__ import annotations

import csv
import json
import os
import re

import numpy as np

_COL_TIME = ("时间", "time", "分钟", "hm", "时刻")
_COL_CODE = ("板块代码", "代码", "code", "secid", "板块id")
_COL_NAME = ("板块名称", "名称", "name", "板块")
_COL_KIND = ("类型", "kind", "类别")
_COL_VAL = ("主力净额", "主力净流入", "净额", "net", "value")


SHELL_MODE_ENV = "FLOWLIVE_SHELL_MODE"
_MODE_PURE = "pure"
_MODE_REPLAY = "replay"


def mode() -> str:
    """当前形态：pure（纯空壳）/ replay（离线复盘终端）。

    与 flowlive-native 用同一个环境变量，两边行为一致。
    """
    v = (os.environ.get(SHELL_MODE_ENV) or _MODE_REPLAY).strip().lower()
    return _MODE_PURE if v == _MODE_PURE else _MODE_REPLAY


def import_enabled() -> bool:
    """纯空壳形态下关闭导入。"""
    return mode() == _MODE_REPLAY


def data_dir() -> str:
    p = os.environ.get("FLOWLIVE_DATA")
    if p and os.path.isdir(p):
        return p
    return "data"


def _pick(header, aliases):
    norm = {re.sub(r"\s+", "", str(h)).lower(): h for h in header}
    for a in aliases:
        k = re.sub(r"\s+", "", a).lower()
        if k in norm:
            return norm[k]
    return None


def _norm_time(s):
    s = str(s).strip()
    m = re.match(r"^(\d{1,2}):?(\d{2})(?::\d{2})?$", s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return "%02d:%02d" % (h, mi)


def _norm_code(s):
    s = str(s).strip()
    if not s:
        return None
    if "." in s:
        return s
    if s.upper().startswith("BK"):
        return "90." + s.upper()
    return s


def import_csv(path: str, tag: str, data_date: str | None = None,
               kind: str = "concept", out_dir: str | None = None):
    """导入 CSV 为面板，返回 (meta, mat)。"""
    if not import_enabled():
        raise RuntimeError("纯空壳形态不提供导入功能（FLOWLIVE_SHELL_MODE=pure）")
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        raise ValueError("CSV 至少需要表头 + 一行数据")

    header = rows[0]
    c_time = _pick(header, _COL_TIME)
    c_code = _pick(header, _COL_CODE)
    c_val = _pick(header, _COL_VAL)
    c_name = _pick(header, _COL_NAME)
    c_kind = _pick(header, _COL_KIND)
    if not (c_time and c_code and c_val):
        raise ValueError("缺少必需列「时间/代码/数值」；当前表头：%s" % header)

    idx = {h: i for i, h in enumerate(header)}
    need = max(idx[c_time], idx[c_code], idx[c_val])
    series, names, kinds = {}, {}, {}
    for r in rows[1:]:
        if len(r) <= need:
            continue
        t = _norm_time(r[idx[c_time]])
        code = _norm_code(r[idx[c_code]])
        if not (t and code):
            continue
        try:
            v = float(str(r[idx[c_val]]).replace(",", "").strip())
        except (TypeError, ValueError):
            continue
        series.setdefault(code, {})[t] = v
        if c_name and r[idx[c_name]].strip():
            names[code] = r[idx[c_name]].strip()
        if c_kind and r[idx[c_kind]].strip():
            kinds[code] = r[idx[c_kind]].strip()

    if not series:
        raise ValueError("没有解析出任何有效数据行")

    times = sorted({t for d in series.values() for t in d})
    codes = sorted(series)
    mat = np.zeros((len(times), len(codes)), dtype=float)
    ti = {t: i for i, t in enumerate(times)}
    for j, code in enumerate(codes):
        for t, v in series[code].items():
            mat[ti[t], j] = v

    meta = {
        "times": times,
        "codes": codes,
        "names": {c: names.get(c, c) for c in codes},
        "kinds": {c: kinds.get(c, kind) for c in codes},
        "source": "import",
        "data_date": data_date or _guess_date(path),
    }

    d = out_dir or data_dir()
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "panel_%s.json" % tag), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    np.save(os.path.join(d, "panel_%s.npy" % tag), mat)
    return meta, mat


def _guess_date(path):
    m = re.search(r"(20\d{6})", os.path.basename(path))
    if m:
        return m.group(1)
    import datetime
    return datetime.date.today().strftime("%Y%m%d")
