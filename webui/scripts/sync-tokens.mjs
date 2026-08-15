#!/usr/bin/env node
/**
 * 设计 token 同步 / 校验（M2.4 Figma 对齐地基）
 *
 * ── 未来 Figma 流程（脚本存在的意义）──
 *   1. 设计 token 在 Figma 中用 Tokens Studio 插件维护
 *   2. 插件导出 JSON（与 src/tokens/tokens.json 同构：{category: {name: {value, type}}}）
 *      → 整体替换 src/tokens/tokens.json
 *   3. npm run tokens:sync → 重生成 src/index.css 的 :root 变量段 → 前端全局生效
 *
 * ── 命令 ──
 *   node scripts/sync-tokens.mjs          # 默认 sync
 *   node scripts/sync-tokens.mjs sync     # tokens.json → index.css :root 段
 *   node scripts/sync-tokens.mjs validate # 校验：tokens.json ↔ :root 双向一致 + hex 归零
 *
 * 校验规则（validate，任一失败 exit 1）：
 *   a. 每个 tokens.json token 都在 :root 中有同名 var 且值一致（方向 A）
 *   b. :root 段每个 var 都能在 tokens.json 中找到（方向 B，防误删）
 *   c. :root 段之外的 CSS 不得出现硬编码 hex（#xxx / #xxxxxx 等）
 *   d. 全文 var(--x) 引用必须在 tokens.json 中有定义（防悬空引用）
 */

import { readFileSync, writeFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const TOKENS_FILE = resolve(ROOT, 'src/tokens/tokens.json')
const CSS_FILE = resolve(ROOT, 'src/index.css')

const MARK_BEGIN = '/* __TOKENS_SYNC_BEGIN__ */'
const MARK_END = '/* __TOKENS_SYNC_END__ */'

/** 分类 → CSS 变量前缀（typography/layout 直接以完整后缀命名） */
const CATEGORY_META = [
  { key: 'color', prefix: '--color-', comment: '颜色：品牌 / 中性 / 文本 / 语义' },
  { key: 'spacing', prefix: '--space-', comment: '间距刻度（4px 基数）' },
  { key: 'typography', prefix: '--', comment: '字体 / 字号 / 字重 / 行高' },
  { key: 'radius', prefix: '--radius-', comment: '圆角' },
  { key: 'shadow', prefix: '--shadow-', comment: '阴影' },
  { key: 'breakpoint', prefix: '--bp-', comment: '断点（仅供 Figma 同步 / 文档；CSS 媒体查询内 var() 不可用，需字面量）' },
  { key: 'duration', prefix: '--duration-', comment: '动效时长' },
  { key: 'easing', prefix: '--ease-', comment: '缓动曲线' },
  { key: 'z-index', prefix: '--z-', comment: '层级' },
  { key: 'layout', prefix: '--', comment: '布局尺寸' },
]

const PREFIX_MAP = Object.fromEntries(CATEGORY_META.map((c) => [c.key, c.prefix]))

function parseTokensJson() {
  const raw = JSON.parse(readFileSync(TOKENS_FILE, 'utf8'))
  const flat = new Map() // varName → value
  const errors = []
  for (const [category, tokens] of Object.entries(raw)) {
    if (category.startsWith('$')) continue // $schema 等元字段
    if (typeof tokens === 'string') continue // description 等元字段（纯字符串描述）
    const prefix = PREFIX_MAP[category]
    if (!prefix) {
      errors.push(`未知分类 ${category}（PREFIX_MAP 未定义，先加 CATEGORY_META）`)
      continue
    }
    if (typeof tokens !== 'object' || tokens === null) {
      errors.push(`分类 ${category} 不是对象`)
      continue
    }
    for (const [name, def] of Object.entries(tokens)) {
      if (name.startsWith('$')) continue
      if (!def || typeof def.value !== 'string') {
        errors.push(`${category}.${name} 缺少 string 类型 value`)
        continue
      }
      flat.set(`${prefix}${name}`, def.value)
    }
  }
  return { flat, errors }
}

function extractRootBlock(css) {
  const begin = css.indexOf(MARK_BEGIN)
  const end = css.indexOf(MARK_END)
  if (begin === -1 || end === -1) return null
  return {
    start: begin,
    end: end + MARK_END.length,
    body: css.slice(begin + MARK_BEGIN.length, css.indexOf(MARK_END)).trim(),
  }
}

function parseCssVars(body) {
  const vars = new Map()
  const re = /(--[\w-]+)\s*:\s*([^;]+);/g
  let m
  while ((m = re.exec(body)) !== null) {
    vars.set(m[1], m[2].trim())
  }
  return vars
}

function generateRootBlock(flat) {
  const lines = []
  lines.push(MARK_BEGIN)
  lines.push(':root {')
  for (const meta of CATEGORY_META) {
    const entries = [...flat.entries()]
      .filter(([name]) => name.startsWith(meta.prefix))
      .sort(([a], [b]) => a.localeCompare(b, 'en', { numeric: true }))
    if (entries.length === 0) continue
    lines.push(`  /* ── ${meta.comment} ── */`)
    for (const [name, value] of entries) {
      lines.push(`  ${name}: ${value};`)
    }
    lines.push('')
  }
  // 去掉末尾空行
  while (lines[lines.length - 1] === '') lines.pop()
  lines.push('}')
  lines.push(MARK_END)
  return lines.join('\n') + '\n'
}

function validate(css, tokensFlat) {
  const errors = []
  const block = extractRootBlock(css)
  if (!block) {
    return { ok: false, errors: ['index.css 缺少 TOKENS-SYNC 标记，请先运行 npm run tokens:sync'] }
  }
  const cssVars = parseCssVars(block.body)

  // a. tokens.json → :root（每个 token 在 CSS 中有 var 且值一致）
  for (const [name, value] of tokensFlat) {
    if (!cssVars.has(name)) {
      errors.push(`[tokens.json→CSS] ${name} 在 :root 段缺失`)
    } else if (cssVars.get(name) !== value) {
      errors.push(`[tokens.json→CSS] ${name} 值不一致: tokens.json=${value} CSS=${cssVars.get(name)}`)
    }
  }
  // b. :root → tokens.json（防误删）
  for (const name of cssVars.keys()) {
    if (!tokensFlat.has(name)) {
      errors.push(`[CSS→tokens.json] ${name} 在 tokens.json 缺失`)
    }
  }
  // c. :root 段之外的 hex 归零
  const outside = css.slice(0, block.start) + css.slice(block.end)
  const hexRe = /#[0-9a-fA-F]{3,8}/g
  const hardHex = outside.match(hexRe) || []
  if (hardHex.length > 0) {
    errors.push(`[hex 归零] :root 段外仍有 ${hardHex.length} 处硬编码 hex: ${[...new Set(hardHex)].join(' ')}`)
  }
  // d. var() 引用必须在 tokens.json 有定义
  const varRefRe = /var\(\s*(--[\w-]+)/g
  const refs = new Set()
  let m
  while ((m = varRefRe.exec(css)) !== null) refs.add(m[1])
  for (const ref of refs) {
    if (!tokensFlat.has(ref)) {
      errors.push(`[悬空引用] var(${ref}) 在 tokens.json 无定义`)
    }
  }
  return { ok: errors.length === 0, errors }
}

function doSync() {
  const { flat, errors } = parseTokensJson()
  if (errors.length > 0) {
    console.error(`tokens.json 解析失败（${errors.length}）:`)
    errors.forEach((e) => console.error(`  ✗ ${e}`))
    process.exit(1)
  }
  let css = readFileSync(CSS_FILE, 'utf8')
  const block = extractRootBlock(css)
  const generated = generateRootBlock(flat)
  if (block) {
    css = css.slice(0, block.start) + generated + css.slice(block.end)
  } else {
    // 无标记：插入到文件头（首个 @import / 注释之后）
    const insertAt = css.indexOf('\n') !== -1 ? css.indexOf('\n') + 1 : 0
    css = css.slice(0, insertAt) + generated + '\n' + css.slice(insertAt)
  }
  // 幂等：END 标记后只留一个空行（否则每次 sync 会多累积一个换行）
  css = css.replace(/(__TOKENS_SYNC_END__ \*\/)\n{3,}/g, '$1\n\n')
  writeFileSync(CSS_FILE, css, 'utf8')
  const v = validate(css, flat)
  if (!v.ok) {
    console.error(`sync 后校验失败（${v.errors.length}）:`)
    v.errors.forEach((e) => console.error(`  ✗ ${e}`))
    process.exit(1)
  }
  console.log(`✓ tokens:sync 完成：${flat.size} 个 token 写入 index.css :root（${TOKENS_FILE} → ${CSS_FILE}）`)
}

function doValidate() {
  const { flat, errors: parseErrors } = parseTokensJson()
  const allErrors = [...parseErrors]
  if (parseErrors.length === 0) {
    const css = readFileSync(CSS_FILE, 'utf8')
    const v = validate(css, flat)
    allErrors.push(...v.errors)
  }
  if (allErrors.length > 0) {
    console.error(`tokens:validate ✗（${allErrors.length} 处不一致）:`)
    allErrors.forEach((e) => console.error(`  ✗ ${e}`))
    process.exit(1)
  }
  const hexOutside = countHexOutsideRoot()
  console.log(`✓ tokens:validate 通过：tokens.json ↔ :root 双向一致（${flat.size} 个 token），:root 外硬编码 hex = ${hexOutside}`)
}

function countHexOutsideRoot() {
  const css = readFileSync(CSS_FILE, 'utf8')
  const block = extractRootBlock(css)
  if (!block) return -1
  const outside = css.slice(0, block.start) + css.slice(block.end)
  return (outside.match(/#[0-9a-fA-F]{3,8}/g) || []).length
}

const cmd = process.argv[2] || 'sync'
if (cmd === 'sync') doSync()
else if (cmd === 'validate') doValidate()
else {
  console.error(`未知命令 ${cmd}（可用: sync / validate）`)
  process.exit(1)
}
