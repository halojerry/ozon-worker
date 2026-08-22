import { useState } from "react"

export default function ProductEditor({
  product,
  onClose,
  source,
  onSaveSource,
}: { product: string; onClose: () => void; source?: { image: string; sourceUrl: string }; onSaveSource?: (sourceUrl: string) => void }) {
  const [tab, setTab] = useState("基础信息")
  const [saved, setSaved] = useState(false)
  const [published, setPublished] = useState(false)
  const [sourceUrl, setSourceUrl] = useState(source?.sourceUrl ?? "")
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="product-drawer listing-editor" role="dialog" aria-modal="true" aria-label="编辑商品" onMouseDown={e => e.stopPropagation()}>
        <header>
          <div><span className="panel-kicker">PRODUCT WORKSPACE</span><h2>编辑商品</h2></div>
          <button onClick={onClose} aria-label="关闭">×</button>
        </header>
        <div className="drawer-product"><div className="product-thumb thumb-0"/><div><b>{product}</b><small>SKU: SPX1-WHT · 草稿已自动保存</small></div><span className="status red">待发布</span></div>
        <nav className="drawer-tabs">{["基础信息", "商品属性", "图文素材", "变体与库存", "发布与定价"].map(x => <button onClick={() => setTab(x)} className={tab === x ? "selected" : ""} key={x}>{x}</button>)}</nav>
        {tab === "基础信息" && <div className="drawer-form"><div className="editor-tip"><b>✦ AI 内容助手</b><span>已根据采集信息补全 82% 字段</span><button>查看建议</button></div><label>商品标题<input defaultValue={product}/><small>AI 生成 · 支持俄语标题优化</small></label>{source && <label>货源地址<input value={sourceUrl} onChange={e => setSourceUrl(e.target.value)} placeholder="https://..."/><small>采集来源字段 · 保存后同步更新采集箱中的货源链接。</small></label>}<label>商品分类<button className="field-button">电子产品 ＞ 耳机　⌄</button></label><div className="drawer-pair"><label>SKU<input defaultValue="SPX1-WHT"/></label><label>品牌<input defaultValue="SoundPro"/></label></div><label>商品卖点<textarea defaultValue="无线降噪耳机，支持 40 小时续航与多设备连接，适用于通勤、运动与居家办公。"/></label></div>}
        {tab === "商品属性" && <div className="drawer-form"><label>型号名称<input defaultValue="SoundPro X1"/></label><label>简述<textarea defaultValue="轻盈舒适的主动降噪无线耳机，兼顾日常与运动场景。"/></label><div className="attribute-list"><b>必填属性 <button>✦ AI 补全属性</button></b><span>颜色 <strong>米白色　×</strong></span><span>蓝牙版本 <strong>5.3　×</strong></span><span>续航时间 <strong>40 小时　×</strong></span></div></div>}
        {tab === "图文素材" && <div className="media-workspace"><div className="media-head"><b>商品素材</b><button>＋ 上传图片</button></div><div className="media-grid"><div className="media-item main">{source ? <img className="editor-source-image" src={source.image} alt="商品主图"/> : <span className="product-thumb thumb-0"/>}<b>主图</b></div><div className="media-item"><span>＋</span><b>场景图</b></div><div className="media-item"><span>＋</span><b>详情图</b></div><div className="media-item"><span>＋</span><b>视频</b></div></div><div className="ai-media-card"><span>◐</span><div><b>AI 图片工坊</b><p>去背景、白底图、尺寸裁剪和场景生成已收敛至商品素材。</p></div><button>打开工具</button></div></div>}
        {tab === "变体与库存" && <div className="variant-workspace"><div className="variant-tools"><b>变体设置</b><button>✦ 批量翻译</button><button>＋ 添加变体</button></div><div className="variant-row header"><span>图片</span><span>货号</span><span>售价 ₽</span><span>库存</span></div><div className="variant-row"><span className="product-thumb thumb-0"/><input defaultValue="SPX1-WHT"/><input defaultValue="2,990"/><input defaultValue="120"/></div><div className="variant-row"><span className="product-thumb thumb-1"/><input defaultValue="SPX1-BLK"/><input defaultValue="2,990"/><input defaultValue="86"/></div></div>}
        {tab === "发布与定价" && <div className="publish-workspace"><div className="pricing-preview"><span>预估毛利</span><b>₽ 1,143.20</b><small>毛利率 38.2% · 基于当前汇率与佣金</small></div><div className="publish-row"><span>上架店铺</span><b>Ozon Russia · 深圳跨境旗舰店</b><button>切换</button></div><div className="publish-row"><span>发布策略</span><b>定时发布 · 2026-08-20 10:00</b><button>编辑</button></div><div className="publish-row"><span>AI 上架检查</span><b className="check-ok">✓ 通过 18 / 20 项检查</b><button>查看</button></div></div>}
        <footer className="editor-footer"><span className="save-state">{saved ? "✓ 草稿已保存" : "自动保存中…"}</span><button className="editor-ai" onClick={() => setTab("基础信息")}>✦ AI 填写商品信息</button><button className="editor-ai" onClick={() => setTab("图文素材")}>◐ AI 商品套图</button><button className="button ghost" onClick={onClose}>关闭</button><button className="button ghost" onClick={() => setSaved(true)}>保存草稿</button><button className="button primary" onClick={() => { setPublished(true); onSaveSource?.(sourceUrl) }}>{published ? "✓ 已提交发布" : "立即上架"}</button></footer>
      </section>
    </div>
  )
}
