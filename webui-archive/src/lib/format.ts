import dayjs from 'dayjs'

/**
 * 金额/数字/百分比/日期格式化（等宽数字配合 font-mono 使用）。
 * spec §03：KPI 大数字 data-lg（28/700 mono），金额 data-md（20/700 mono）。
 */

/** 金额：¥ 1,234.50（CNY 店铺） */
export function formatCurrency(value: number | string | null | undefined, currency = 'CNY'): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  const n = Number(value)
  const symbol = currency === 'CNY' ? '¥' : currency === 'RUB' ? '₽' : '$'
  return `${symbol} ${new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n)}`
}

/** 金额（人民币，无小数位的场景） */
export function formatYuan(value: number | string | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  return `¥ ${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(Number(value))}`
}

/** 千分位数字：1,284 */
export function formatNumber(value: number | string | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  return new Intl.NumberFormat('zh-CN').format(Number(value))
}

/** 百分比：12.4% */
export function formatPercent(value: number | string | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  return `${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits }).format(Number(value))}%`
}

/** 日期：2026-08-18 14:30 */
export function formatDateTime(value: string | number | Date | null | undefined): string {
  if (!value) return '—'
  const d = dayjs(value)
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm') : '—'
}

/** 日期：2026-08-18 */
export function formatDate(value: string | number | Date | null | undefined): string {
  if (!value) return '—'
  const d = dayjs(value)
  return d.isValid() ? d.format('YYYY-MM-DD') : '—'
}

/** 相对时间：5 分钟前 */
export function formatRelative(value: string | number | Date | null | undefined): string {
  if (!value) return '—'
  const d = dayjs(value)
  if (!d.isValid()) return '—'
  const diffMin = dayjs().diff(d, 'minute')
  if (diffMin < 1) return '刚刚'
  if (diffMin < 60) return `${diffMin} 分钟前`
  const diffHour = dayjs().diff(d, 'hour')
  if (diffHour < 24) return `${diffHour} 小时前`
  const diffDay = dayjs().diff(d, 'day')
  if (diffDay < 30) return `${diffDay} 天前`
  return d.format('YYYY-MM-DD')
}

/** 时长（毫秒）→ 人读：3 分 20 秒 */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(Number(ms))) return '—'
  const totalSec = Math.round(Number(ms) / 1000)
  if (totalSec < 60) return `${totalSec}s`
  const min = Math.floor(totalSec / 60)
  const sec = totalSec % 60
  if (min < 60) return `${min} 分 ${sec} 秒`
  const hour = Math.floor(min / 60)
  return `${hour} 小时 ${min % 60} 分`
}
