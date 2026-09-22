#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# sign_cache_hashes.sh — 缓存 JSON 哈希的生产者：重算 sha256 注入 manifest 并重签
# （v0.76 T32 评审 Medium-2：cd.yml 只从上一份 manifest 继承 cache_sha256，首发
# 恒空、空表永续继承——本脚本是空表的破局工具，生产 hash 源接入点在 warm 导出
# 侧，接线说明落 CACHE-WARM-RUNBOOK（T33 文档项）。）
#
# 用法:
#   sign_cache_hashes.sh <cache_dir> <manifest_in> <sec_key> <out_dir> [pub]
#
#   <cache_dir>    含缓存 JSON 的目录（每个 *.json 逐个登记，如
#                  attribute_schemas_zh.json / dictionary_values_zh.json）
#   <manifest_in>  当前（已验签过的）manifest.json
#   <sec_key>      minisign 私钥文件（无密码口径，同 CI COS_UPDATE_SIGN_KEY）
#   <out_dir>      输出目录（写出 manifest.json + manifest.sig，不原地改写）
#   [pub]          可选 minisign 公钥——给出时用 verify_manifest.sh 回验自检
#
# 语义:
#   - cache_sha256 整表替换（生产哈希源口径）：以 cache_dir 现存 *.json 为准，
#     已不在目录中的陈旧条目随之清除
#   - 输出 manifest 为单行紧凑 JSON（与 cd.yml 生成口径一致；服务器端解析
#     另有压平兜底）
#   - 退出码: 0 成功 / 2 用法·环境错误 / 3 签名或回验失败
#
# 产出后上传（成对覆盖，缺一不可）:
#   coscli cp <out_dir>/manifest.json  cos://<bucket>/ozon-worker/manifest.json
#   coscli cp <out_dir>/manifest.sig   cos://<bucket>/ozon-worker/manifest.sig
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

usage() {
  echo "usage: $0 <cache_dir> <manifest_in> <sec_key> <out_dir> [pub]" >&2
  exit 2
}
fail() { echo -e "\033[1;31m[sign-cache]\033[0m ❌ $*" >&2; exit "${2:-2}"; }

[ "$#" -ge 4 ] && [ "$#" -le 5 ] || usage
CACHE_DIR="$1"
MANIFEST_IN="$2"
SEC_KEY="$3"
OUT_DIR="$4"
PUB="${5:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ -d "$CACHE_DIR" ]      || fail "缓存目录不存在: $CACHE_DIR"
[ -f "$MANIFEST_IN" ]    || fail "manifest 不存在: $MANIFEST_IN"
[ -f "$SEC_KEY" ]        || fail "私钥不存在: $SEC_KEY"
command -v minisign >/dev/null 2>&1 || fail "minisign 不可用（请安装）" 2

# ── 1. 收集缓存文件哈希（确定性排序；缓存文件名为固定 ASCII 无空白，可安全分词）──
JSON_LIST=$(find "$CACHE_DIR" -maxdepth 1 -type f -name '*.json' | sort)
[ -n "$JSON_LIST" ] || fail "cache_dir 内无 .json 文件: $CACHE_DIR"

# sha256 摘要（macOS 无 sha256sum 时回落 shasum -a 256）
_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

CACHE_OBJ="{"
_sep=""
_count=0
for _f in $JSON_LIST; do
  _name=$(basename "$_f")
  _sha=$(_sha256 "$_f")
  CACHE_OBJ="${CACHE_OBJ}${_sep}\"${_name}\": \"${_sha}\""
  _sep=","
  _count=$((_count + 1))
done
CACHE_OBJ="${CACHE_OBJ}}"

# ── 2. 注入 cache_sha256（整表替换）→ 临时文件 + 原子 mv ──
mkdir -p "$OUT_DIR"
_OUT_TMP=$(mktemp "$OUT_DIR/manifest.json.XXXXXX")
if command -v python3 >/dev/null 2>&1; then
  MANIFEST_IN="$MANIFEST_IN" OUT_TMP="$_OUT_TMP" CACHE_OBJ="$CACHE_OBJ" python3 - <<'PYEOF'
import json
import os

with open(os.environ["MANIFEST_IN"], encoding="utf-8") as f:
    manifest = json.load(f)
manifest["cache_sha256"] = json.loads(os.environ["CACHE_OBJ"])
with open(os.environ["OUT_TMP"], "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False)
PYEOF
elif command -v jq >/dev/null 2>&1; then
  jq --argjson cache "$CACHE_OBJ" '.cache_sha256 = $cache' "$MANIFEST_IN" > "$_OUT_TMP"
else
  rm -f "$_OUT_TMP"
  fail "注入 cache_sha256 需要 python3 或 jq 之一（均不可用）" 2
fi
[ -s "$_OUT_TMP" ] || { rm -f "$_OUT_TMP"; fail "manifest 注入后为空"; }
mv "$_OUT_TMP" "$OUT_DIR/manifest.json"

# ── 3. 重签 + 可选回验 ──
if ! minisign -S -s "$SEC_KEY" -x "$OUT_DIR/manifest.sig" -m "$OUT_DIR/manifest.json"; then
  fail "minisign -S 签名失败（检查私钥格式；CI 口径为无密码私钥）" 3
fi
if [ -n "$PUB" ]; then
  set +e
  bash "$SCRIPT_DIR/verify_manifest.sh" "$PUB" "$OUT_DIR/manifest.json" "$OUT_DIR/manifest.sig"
  _rc=$?
  set -e
  [ "$_rc" -eq 0 ] || fail "回验失败(verify_manifest.sh exit $_rc)——产出不可信，勿上传" 3
fi

echo "✅ cache_sha256 已注入并重签（$_count 个文件）:"
for _f in $JSON_LIST; do
  echo "    $(basename "$_f") = $(_sha256 "$_f")"
done
echo "📤 产出: $OUT_DIR/manifest.json + $OUT_DIR/manifest.sig（上传须成对覆盖 COS /ozon-worker/）"
exit 0
