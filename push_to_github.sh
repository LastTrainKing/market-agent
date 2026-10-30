#!/usr/bin/env bash
# 一条命令建仓并推送 market-agent
# 前提：本机 git 已能访问 GitHub（例如 git config --global http.proxy http://127.0.0.1:7890）
set -e
cd "$(dirname "$0")"

git init -q 2>/dev/null || true
git add -A
git -c user.name="LastTrainKing" -c user.email="lasttrainking@users.noreply.github.com" \
    commit -q -m "feat: market-agent 初版（离线复盘 Agent 工作台）

- agent_core：权限闸门 / 工具注册 / 审计 / 运行循环 / 离线 Provider
- market_tools：只读本地面板，零网络
- 三个内置 Agent + 自定义 Agent 持久化
- 10 项测试全绿

边界：工具集不联网，行情只从本地导入。"
git branch -M main

# 先在 GitHub 网页建好空仓库 market-agent，再执行下面这行
git remote add origin git@github.com:LastTrainKing/market-agent.git 2>/dev/null || \
    git remote set-url origin git@github.com:LastTrainKing/market-agent.git
git push -u origin main
