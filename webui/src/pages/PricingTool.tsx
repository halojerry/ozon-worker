import { useState } from 'react'
import { estimateEnvelope, type Envelope } from '../api/client'

/* P2a 定价工具：输入成本/重量/尺寸/利润率 → worker 同源公式预估（前端不写公式） */

interface EstimateInput {
  cost: string
  weight: string
  length: string
  width: string
  height: string
  marginRate: string
  commissionRate: string
  fxBuffer: string
}

const EMPTY_INPUT: EstimateInput = {
  cost: '',
  weight: '',
  length: '',
  width: '',
  height: '',
  marginRate: '25',
  commissionRate: '',
  fxBuffer: '5',
}

function extractError(err: unknown, fallback: string): string {
  const resp = (err as { response?: { data?: { detail?: string } } } | null)?.response
  return resp?.data?.detail || fallback
}

export default function PricingTool() {
  const [input, setInput] = useState<EstimateInput>(EMPTY_INPUT)
  const [result, setResult] = useState<{ price: number; old_price?: number; profit_cny: number; profit_rate: number; logistics_cost_cny?: number; currency: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  function set<K extends keyof EstimateInput>(key: K, value: string) {
    setInput((f) => ({ ...f, [key]: value }))
  }

  async function calculate() {
    if (!input.cost || !input.weight) {
      setError('请填写采购成本和重量（必填）')
      return
    }
    setBusy(true)
    setError('')
    const envelope: Envelope = {
      draft: {
        purchase_cost: Number(input.cost),
        weight: Number(input.weight),
        dimensions: {
          length: Number(input.length) || 0,
          width: Number(input.width) || 0,
          height: Number(input.height) || 0,
        },
      },
      extensions: {},
    }
    try {
      const res = await estimateEnvelope({
        envelope,
        margin_rate: input.marginRate ? Number(input.marginRate) / 100 : undefined,
        commission_rate: input.commissionRate ? Number(input.commissionRate) / 100 : undefined,
        fx_buffer: input.fxBuffer ? Number(input.fxBuffer) / 100 : undefined,
      })
      setResult(res)
    } catch (e) {
      setError(extractError(e, '计算失败'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <h1 className="page-title">定价工具</h1>
        <span className="page-badge">P2a</span>
      </header>

      <div className="card" style={{ maxWidth: '640px' }}>
        <div className="card-body" style={{ padding: '20px' }}>
          <div className="field">
            <label className="field-label" htmlFor="pt-cost">采购成本（CNY）*</label>
            <input id="pt-cost" className="field-input" type="number" min="0" step="any" placeholder="如 12.5" value={input.cost} onChange={(e) => set('cost', e.target.value)} />
          </div>
          <div className="form-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
            <div className="field">
              <label className="field-label" htmlFor="pt-weight">重量（克）*</label>
              <input id="pt-weight" className="field-input" type="number" min="0" placeholder="如 350" value={input.weight} onChange={(e) => set('weight', e.target.value)} />
            </div>
            <div className="field">
              <label className="field-label">尺寸（长×宽×高，mm，选填）</label>
              <div style={{ display: 'flex', gap: '6px' }}>
                <input className="field-input" type="number" min="0" placeholder="长" value={input.length} onChange={(e) => set('length', e.target.value)} />
                <input className="field-input" type="number" min="0" placeholder="宽" value={input.width} onChange={(e) => set('width', e.target.value)} />
                <input className="field-input" type="number" min="0" placeholder="高" value={input.height} onChange={(e) => set('height', e.target.value)} />
              </div>
            </div>
          </div>
          <div className="form-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '12px' }}>
            <div className="field">
              <label className="field-label" htmlFor="pt-margin">利润率（%）</label>
              <input id="pt-margin" className="field-input" type="number" min="0" max="100" value={input.marginRate} onChange={(e) => set('marginRate', e.target.value)} />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="pt-commission">佣金率（%）</label>
              <input id="pt-commission" className="field-input" type="number" min="0" max="50" placeholder="空=自动查" value={input.commissionRate} onChange={(e) => set('commissionRate', e.target.value)} />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="pt-fx">汇率缓冲（%）</label>
              <input id="pt-fx" className="field-input" type="number" min="0" max="50" value={input.fxBuffer} onChange={(e) => set('fxBuffer', e.target.value)} />
            </div>
          </div>
          {error && <div className="form-error" role="alert"><span>{error}</span></div>}
          <button className="btn btn-primary" onClick={calculate} disabled={busy}>
            {busy ? '计算中…' : '开始计算'}
          </button>

          {result && (
            <div style={{ marginTop: '20px', paddingTop: '16px', borderTop: '1px solid var(--color-border)' }}>
              <div className="order-detail-grid">
                <div><span className="order-detail-label">建议售价</span><strong>{result.currency} {result.price}</strong></div>
                <div><span className="order-detail-label">划线价</span>{result.old_price != null ? `${result.currency} ${result.old_price}` : '—'}</div>
                <div><span className="order-detail-label">预估利润</span>{result.currency} {result.profit_cny.toFixed(2)}</div>
                <div><span className="order-detail-label">利润率</span>{(result.profit_rate * 100).toFixed(1)}%</div>
                <div><span className="order-detail-label">物流费</span>{result.logistics_cost_cny != null ? `¥${result.logistics_cost_cny.toFixed(2)}` : '—'}</div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
