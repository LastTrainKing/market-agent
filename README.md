# market-agent

A 股**离线复盘** Agent 工作台。内置三个分析师 Agent，支持自建与私人定制。

> **一句话边界**：Agent 能分析，但拿不到新行情。
> 行情数据只从本地导入——因为上游接口已对爬虫来源做风控标记，
> 联网取数会导致运行机器的 IP 被封禁。

---

## 它是什么

一个类 dsh / WorkBuddy 的 Agent 运行时，专为「离线复盘」这一个场景做减法：

- **三个内置 Agent**：复盘助手 / 盘面解读 / 研究助理
- **用户可自建**：名字、人格、工具白名单、模型、权限等级，存在 `agents.json` 里，可手改
- **工具集不联网**：全部只读本地面板数据
- **每次运行留审计**：任何结论都能回溯到它的输入证据

---

## 快速开始

```bash
# 1. 导入一份本地数据（CSV 需要「时间/代码/数值」三列）
python cli.py import demo/sample_20261030.csv 20261030

# 2. 看有哪些 Agent
python cli.py list

# 3. 跑一次复盘（离线模式，不需要 API Key）
python cli.py run reviewer "复盘最新一天"

# 4. 看审计
python cli.py audit
```

真实输出：

```
# 复盘报告 · 20261030

数据来源：import（本地导入，未联网）
覆盖：3 分钟 × 3 板块

## 辨识度前 5
1. 半导体（90.BK1036） 评分 1.000 · 均净额 1.80 亿 · 活跃度 100%
2. 房地产（90.BK1202） 评分 0.342 · 均净额 0.40 亿 · 活跃度 100%
3. 白酒（90.BK0801） 评分 0.200 · 均净额 0.10 亿 · 活跃度 100%
```

---

## 架构

```
┌──────────────────────────────────────────────┐
│  CLI / (未来) 前端                            │
└───────────────────┬──────────────────────────┘
                    │
┌───────────────────▼──────────────────────────┐
│  agent_core                                   │
│  ┌──────────┬──────────┬──────────┬────────┐ │
│  │ providers│ tools    │permission│ audit  │ │
│  │ 离线/兼容 │ 注册+执行 │ 唯一闸门  │ JSONL  │ │
│  └──────────┴──────────┴──────────┴────────┘ │
│  agent.py  ── 运行循环（步数上限 / 拒绝无效重试）│
│  builtin.py ── 三个内置 Agent + 自建入口        │
└───────────────────┬──────────────────────────┘
                    │
┌───────────────────▼──────────────────────────┐
│  market_tools.py   ← 只读本地，零网络          │
│  list_panels / read_panel / run_salience      │
│  query_history / write_report                 │
└───────────────────┬──────────────────────────┘
                    │
              panel_*.json + .npy
              （与 flowlive-native 格式一致）
```

---

## 三条硬规则

这三条不是口号，是被真实事故逼出来的，代码里都有对应断言。

### 1. 裁决是证据，不是意见

一步成不成功，看工具返回的 `ok`，不看模型怎么说。
结论必须挂在至少一次成功的工具调用上——没有工具结果支撑的"我分析完了"，不算完成。

### 2. 重试不是循环

连续两次相同工具 + 相同参数，**第二次直接拒绝执行**。

> 这条来自一次真实事故：不加它，一次运行在两个步骤里烧掉上百次调用，
> 每次都用同样的 prompt、同样的工具、同样的参数，失败得一模一样。

测试：`test_identical_retry_refused`

### 3. 产出不等于交付

`verified`（机器校验）与 `accepted`（人点头）是两个字段。
本模块只负责前者。

---

## 权限模型

**一个地方决定 Agent 能碰什么。** 所有工具调用过同一个闸门，没有旁路。

| 层 | 规则 |
|---|---|
| 等级 | `none < read < write < exec`，默认 `read` |
| 工具白名单 | `spec.tools` 既是"给模型看的清单"，**也是闸门的允许列表** |
| 路径沙箱 | 所有带 `path` 的参数必须落在 workspace 内 |
| 写入配额 | 单次运行写文件上限（默认 200） |
| 网络 | **硬编码 False，构造时传 True 直接抛异常** |

```python
# 这一行会抛 ValueError
PermissionGate(Permission(network=True))
```

---

## 自定义 Agent

```bash
python cli.py new my_agent "我的复盘助手" --level read --persona "只看半导体，别的不谈"
```

生成的 `agents.json` 可以直接手改：

```json
{
  "id": "my_agent",
  "name": "我的复盘助手",
  "persona": "只看半导体，别的不谈",
  "provider": "offline",
  "tools": [],
  "deny_tools": [],
  "level": "read",
  "max_steps": 12,
  "workspace": ".",
  "builtin": false
}
```

`tools` 为空表示"全部允许"（仍受 level 约束）；填了就是白名单。

---

## 接入真实模型

默认 `offline` 模式不需要任何 Key，用确定性规则跑通链路。
要接模型：

```bash
set AGENT_PROVIDER=deepseek
set AGENT_API_KEY=你的Key
set AGENT_BASE_URL=https://api.deepseek.com/v1
python cli.py run reviewer "复盘最新一天"
```

**项目不内置任何 Key。** 模型调用与行情取数是两件事：
前者用你自己的资源，后者本产品根本不做。

---

## 数据格式

与 `flowlive-native` 完全一致，两边数据可互通：

```
panel_20261030.json  ->  {times, codes, names, kinds, source, data_date}
panel_20261030.npy   ->  shape (len(times), len(codes))
```

也可以直接指向 flowlive 的数据目录：

```bash
set FLOWLIVE_DATA=D:\...\flowlive-native\build\core\data
```

---

## 测试

不需要 pytest：

```bash
python tests/test_core.py
```

实测输出（2026-10-30 重跑）：

```
== 权限 ==
  ✓ 联网开关无法打开
  ✓ 等级不足被拒
  ✓ 路径越界被拒
  ✓ 白名单外被拒

== 数据与工具 ==
  ✓ CSV 导入 + 辨识度
  ✓ 越权调用被记录

== Agent 循环 ==
  ✓ 离线端到端跑通
  ✓ 无效重试被拒
  ✓ 模型输出解析
  ✓ 自定义 Agent 持久化

==================================================
通过 10 项，失败 0 项
```

退出码 0。CI 同源步骤见 [`.github/workflows/test.yml`](.github/workflows/test.yml)，
最近一次 [run 41040154868](https://github.com/LastTrainKing/market-agent/actions/runs/41040154868) 为 success。

---

## 进一步阅读

| 文档 | 内容 |
|---|---|
| 本文件 | 快速开始、架构、三条硬规则、权限模型、数据格式 |
| [`agent_core/permissions.py`](agent_core/permissions.py) | 权限四层判定的实现与注释 |
| [`agent_core/agent.py`](agent_core/agent.py) | 运行循环与三条硬规则的落地位置 |
| [`market_tools.py`](market_tools.py) | 工具集实现（只读本地，零网络） |
| [`tests/test_core.py`](tests/test_core.py) | 10 项测试，可直接当行为规格读 |

上游看盘项目（数据格式来源）：
[`realtime-market-monitor`](https://github.com/LastTrainKing/realtime-market-monitor) ·
其 Shell 版总入口
[`docs/SHELL.md`](https://github.com/LastTrainKing/realtime-market-monitor/blob/shell/no-data-read/docs/SHELL.md)。

---

## 已知限制（如实标注）

1. **辨识度是简化版**：体量/强度/持续三维加权，不是 flowlive-native 的完整五维算法。
   完整版含题材聚类与事件检测，请以原工程为准。
2. **离线 Provider 是规则引擎**：能跑通链路、可复现，但不会真正"理解"目标。
   接真实模型后才有推理能力。
3. **没有前端**：当前只有 CLI。前端是下一阶段工作。
4. **未打包**：无 exe、无安装包。

---

## 免责

本项目为学习研究用途，不构成投资建议。
