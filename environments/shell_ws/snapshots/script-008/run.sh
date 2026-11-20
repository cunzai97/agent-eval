#!/usr/bin/env bash
# 复现入口（交接稿原文）：管线代码在仓库根目录的 project/ 子目录下。
# 上游一直是用这个脚本一键复算日报的。
set -euo pipefail

cd project
python3 main.py
