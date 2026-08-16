/**
 * 业务页共享格式化（S2.2 抽取：fmtTime 7 页 / fmtMoney 3 页 / fmtRate 2 页 原各自实现）
 *
 * 统一格式：
 * - fmtTime：ISO 字符串 → 'YYYY-MM-DD HH:mm'，无效/空返回 '—'
 * - fmtMoney：数值 → 符号 + 2 位小数，默认 ¥（可传 'RUB'/'USD' 或字面符号）
 * - fmtRate：比率 → 百分比 1 位小数
 * 调用方不得在页面内重新实现。
 */

const CURRENCY_SYMBOL: Record<string, string> = { CNY: '¥', RUB: '₽', USD: '$' }

/** ISO 时间 → 'YYYY-MM-DD HH:mm'；空/无效返回 '—' */
export function fmtTime(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 金额 → 符号 + 2 位小数；null/无效返回 '—'；currency 传 'RUB' 等或直接传 '₽' */
export function fmtMoney(v: number | null | undefined, currency?: string): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—'
  const sym = (currency && CURRENCY_SYMBOL[currency]) || currency || '¥'
  return `${sym}${v.toFixed(2)}`
}

/** 比率（0.12）→ '12.0%'；无效返回 '—' */
export function fmtRate(v: number | undefined): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—'
  return `${(v * 100).toFixed(1)}%`
}
