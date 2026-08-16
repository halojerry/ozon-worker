/**
 * 业务页共享组件（S2.2 抽取：ImageCell 3 页 / 预估懒加载 Home+CollectBox 原各自实现）
 *
 * - ImageCell：图片单元格（加载失败占位）
 * - loadEstimate / EstimateBadges：草稿预估懒加载（模块级 Promise 缓存 + 并发节流）
 * 调用方不得在页面内重新实现。
 */
import { useState } from 'react'
import { fmtMoney, fmtRate } from './format'
import type { DraftEstimate } from '@/api/client'

/** 图片单元格：无图/加载失败 → 占位图标 */
export function ImageCell({ src, alt }: { src?: string; alt: string }) {
  const [broken, setBroken] = useState(false)
  if (!src || broken) {
    return (
      <div className="img-placeholder" role="img" aria-label={`图片加载失败：${alt}`}>
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6">
          <rect x="3.5" y="3.5" width="17" height="17" rx="2.5" />
          <circle cx="9" cy="9" r="1.8" />
          <path d="M4.5 18.5l5-5 3.5 3.5 3-3 3.5 3.5" />
        </svg>
      </div>
    )
  }
  return <img className="draft-thumb" src={src} alt={alt} loading="lazy" onError={() => setBroken(true)} />
}

/* ── 预估懒加载：模块级 Promise 缓存去重 + 并发节流（同 CollectBox） ── */
const estimateCache = new Map<string, Promise<DraftEstimate | null>>()
const ESTIMATE_MAX_IN_FLIGHT = 4
let estimateInFlight = 0
const estimateWaiters: Array<() => void> = []

function acquireEstimateSlot(): Promise<void> {
  if (estimateInFlight < ESTIMATE_MAX_IN_FLIGHT) {
    estimateInFlight += 1
    return Promise.resolve()
  }
  return new Promise((resolve) => {
    estimateWaiters.push(() => {
      estimateInFlight += 1
      resolve()
    })
  })
}

function releaseEstimateSlot(): void {
  estimateInFlight -= 1
  estimateWaiters.shift()?.()
}

export function loadEstimate(draftId: string): Promise<DraftEstimate | null> {
  const cached = estimateCache.get(draftId)
  if (cached) return cached
  const promise = (async () => {
    await acquireEstimateSlot()
    try {
      // 延迟 import 防循环依赖（该模块被 Home/CollectBox 引用，client.ts 不回引）
      const { estimateDraft } = await import('@/api/client')
      const est = await estimateDraft(draftId)
      return est ?? null
    } catch {
      return null
    } finally {
      releaseEstimateSlot()
    }
  })()
  estimateCache.set(draftId, promise)
  return promise
}

/** 预估徽章：懒加载后展示 售价/利润/利润率（Home 卡片用，样式 home-est* 见 index.css） */
export function EstimateBadges({ draftId }: { draftId: string }) {
  const [est, setEst] = useState<DraftEstimate | null>(null)
  const [loaded, setLoaded] = useState(false)
  if (!loaded) {
    loadEstimate(draftId).then((e) => {
      setEst(e)
      setLoaded(true)
    })
    return <span className="status-muted">预估中…</span>
  }
  if (!est) {
    return <span className="status-muted">无预估</span>
  }
  return (
    <span className="home-estimate" title="预估售价 / 预估利润 / 利润率（worker 定价引擎）">
      <span className="home-est-price">售价 {fmtMoney(est.price, est.currency)}</span>
      <span className="home-est-profit">利润 {fmtMoney(est.profit_cny, est.currency)}</span>
      <span className="home-est-rate">率 {fmtRate(est.profit_rate)}</span>
    </span>
  )
}
