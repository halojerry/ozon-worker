#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# verify_manifest.sh — COS 升级分发 manifest 签名校验（v0.76 T32 cicd-H2）
#
# 信任根收口: 此前 manifest 与包同源(COS bucket 写权限=信任根), 谁能写 bucket
# 谁就能让生产服务器执行恶意包。本脚本把信任根收口到 minisign 公钥:
# `minisign -V -p <pub> -x <sig> -m <manifest>` 失败 → 拒绝(不改任何状态)。
#
# 用法:
#   verify_manifest.sh <pub> <manifest> <sig> [expected_version]
#
#   <pub>              minisign 公钥文件(生产 = deploy/cos-update.pub)
#   <manifest>         manifest.json 本地文件
#   <sig>              manifest.sig(minisign -S 产出, 与 manifest 同源分发)
#   [expected_version] 可选——manifest 内 version 字段必须等于该值
#                      (两侧统一剥 v 前缀比较, v0.77.0 == 0.77.0)
#
# 退出码(调用方 cos-update.sh 据此分诊):
#   0  验签通过(且 expected_version 给定时版本匹配)
#   2  用法/环境错误(参数缺失/文件不存在/minisign 不可用/version 字段无法解析)
#   3  签名校验失败(信任根失败——绝不继续)
#   4  版本不匹配
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

usage() { echo "usage: $0 <pub> <manifest> <sig> [expected_version]" >&2; exit 2; }

[ "$#" -ge 3 ] && [ "$#" -le 4 ] || usage
PUB="$1"
MANIFEST="$2"
SIG="$3"
EXPECTED_VERSION="${4:-}"

[ -f "$PUB" ]      || { echo "verify_manifest: 公钥文件不存在: $PUB" >&2; exit 2; }
[ -f "$MANIFEST" ] || { echo "verify_manifest: manifest 不存在: $MANIFEST" >&2; exit 2; }
[ -f "$SIG" ]      || { echo "verify_manifest: 签名文件不存在: $SIG" >&2; exit 2; }
[ -s "$SIG" ]      || { echo "verify_manifest: 签名文件为空: $SIG" >&2; exit 2; }
command -v minisign >/dev/null 2>&1 \
  || { echo "verify_manifest: minisign 不可用——服务器需安装 minisign(opkg/apt/brew 或静态二进制)" >&2; exit 2; }

# ── 1. 签名校验(信任根 = 公钥文件) ──
if ! minisign -V -q -p "$PUB" -x "$SIG" -m "$MANIFEST" >/dev/null 2>&1; then
  echo "verify_manifest: 签名校验失败——manifest 与签名不匹配, 或签名者不是受信公钥(可能被篡改)" >&2
  exit 3
fi

# ── 2. 可选: expected_version 与 manifest 内 version 字段比对 ──
# 解析形态: python3 → jq → grep(与 cos-update.sh 既有解析口径一致的三级回落)
if [ -n "$EXPECTED_VERSION" ]; then
  MANIFEST_VERSION=""
  if command -v python3 >/dev/null 2>&1; then
    MANIFEST_VERSION=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version",""))' "$MANIFEST" 2>/dev/null || true)
  elif command -v jq >/dev/null 2>&1; then
    MANIFEST_VERSION=$(jq -r '.version // empty' "$MANIFEST" 2>/dev/null || true)
  else
    MANIFEST_VERSION=$(grep -oE '"version"[[:space:]]*:[[:space:]]*"[^"]+"' "$MANIFEST" 2>/dev/null | head -1 | sed 's/.*"\([^"]*\)"$/\1/' || true)
  fi
  [ -n "$MANIFEST_VERSION" ] || { echo "verify_manifest: manifest 无 version 字段(且无 python3/jq 可用)" >&2; exit 2; }
  WANT="${EXPECTED_VERSION#v}"
  GOT="${MANIFEST_VERSION#v}"
  if [ "$WANT" != "$GOT" ]; then
    echo "verify_manifest: 版本不匹配——manifest=$GOT 期望=$WANT" >&2
    exit 4
  fi
fi

exit 0
