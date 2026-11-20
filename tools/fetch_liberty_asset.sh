#!/usr/bin/env bash
# 拉取 liberty-extract-001 所需的只读 PDK 资产。
# 该 .lib 有 66MB 且属 GF/ARM PDK 授权数据，因此不进 git。
# 任务的断言值（cell_count=1039, timing_group_count=13760 等）严格对应该文件。
set -euo pipefail

SRC="${LIBERTY_SRC:?请用 LIBERTY_SRC 指向你们的 sc9_cmos28lp HVT tt 库（.lib），例如 .../lib/sc9_cmos28lp_base_hvt_tt_nominal_max_1p10v_25c.lib}"
DST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../assets/liberty" && pwd)"
DST="$DST_DIR/$(basename "$SRC")"
EXPECTED_BYTES=69497039

if [[ ! -f "$SRC" ]]; then
  echo "找不到源文件: $SRC" >&2
  echo "可用 LIBERTY_SRC=<path> 覆盖路径。" >&2
  exit 1
fi

if [[ -f "$DST" && "$(stat -c%s "$DST")" == "$EXPECTED_BYTES" ]]; then
  echo "已就位: $DST ($EXPECTED_BYTES bytes)"
  exit 0
fi

cp "$SRC" "$DST.tmp"
actual="$(stat -c%s "$DST.tmp")"
if [[ "$actual" != "$EXPECTED_BYTES" ]]; then
  echo "大小不符: 期望 $EXPECTED_BYTES, 实际 $actual" >&2
  rm -f "$DST.tmp"
  exit 1
fi
mv "$DST.tmp" "$DST"
chmod 444 "$DST"
echo "已安装: $DST ($actual bytes, 只读)"
