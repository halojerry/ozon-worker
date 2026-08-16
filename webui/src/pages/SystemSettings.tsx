import { useCallback, useEffect, useState, type ChangeEvent, type FormEvent } from 'react'
import {
  createSiteAnnouncement,
  createSiteBanner,
  deleteQuery,
  deleteSiteAnnouncement,
  deleteSiteBanner,
  getConfig,
  importQueries,
  listConfigBackups,
  listConfigs,
  listQueries,
  listSiteAnnouncements,
  listSiteBanners,
  putConfig,
  rollbackConfig,
  updateSiteAnnouncement,
  updateSiteBanner,
  type ConfigBackupItem,
  type ConfigFileItem,
  type QueryRow,
  type SiteAnnouncement,
  type SiteAnnouncementInput,
  type SiteBanner,
  type SiteBannerInput,
} from '../api/client'
import { useNavigate } from '@/lib/router-compat'
import { fmtTime } from '../lib/business/format'
import { extractError } from '../lib/business/errors'

type TabId = 'site' | 'config' | 'queries' | 'business'

const QUERY_PAGE = 50

function EnabledBadge({ enabled }: { enabled: boolean }) {
  return <span className={`badge ${enabled ? 'badge-ok' : 'badge-fail'}`}>{enabled ? '启用' : '停用'}</span>
}

export default function SystemSettings() {
  const navigate = useNavigate()
  const [tab, setTab] = useState<TabId>('site')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // ── 站点运营 ──
  const [banners, setBanners] = useState<SiteBanner[]>([])
  const [announcements, setAnnouncements] = useState<SiteAnnouncement[]>([])
  const [bannerModal, setBannerModal] = useState<{ editing: SiteBanner | null } | null>(null)
  const [bannerForm, setBannerForm] = useState<SiteBannerInput>({
    image_url: '',
    link_url: '',
    title: '',
    sort_order: 0,
    enabled: true,
  })
  const [bannerSaving, setBannerSaving] = useState(false)
  const [bannerFormError, setBannerFormError] = useState('')
  const [announcementModal, setAnnouncementModal] = useState<{ editing: SiteAnnouncement | null } | null>(null)
  const [announcementForm, setAnnouncementForm] = useState<SiteAnnouncementInput>({
    title: '',
    content: '',
    announcement_type: 'banner',
    enabled: true,
  })
  const [announcementSaving, setAnnouncementSaving] = useState(false)
  const [announcementFormError, setAnnouncementFormError] = useState('')

  // ── 引擎配置 ──
  const [configs, setConfigs] = useState<ConfigFileItem[]>([])
  const [activeConfig, setActiveConfig] = useState('')
  const [configContent, setConfigContent] = useState('')
  const [configNotice, setConfigNotice] = useState('')
  const [configError, setConfigError] = useState('')
  const [configSaving, setConfigSaving] = useState(false)
  const [backups, setBackups] = useState<ConfigBackupItem[]>([])
  const [rollbackTarget, setRollbackTarget] = useState<ConfigBackupItem | null>(null)
  const [rollbackBusy, setRollbackBusy] = useState(false)

  // ── 选品库 ──
  const [queries, setQueries] = useState<QueryRow[]>([])
  const [queryTotal, setQueryTotal] = useState(0)
  const [querySearch, setQuerySearch] = useState('')
  const [queryOffset, setQueryOffset] = useState(0)
  const [queryError, setQueryError] = useState('')
  const [queryDeleting, setQueryDeleting] = useState<number | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [importCsv, setImportCsv] = useState('')
  const [importResult, setImportResult] = useState('')
  const [importError, setImportError] = useState('')
  const [importBusy, setImportBusy] = useState(false)

  const loadSite = useCallback(async () => {
    try {
      const [b, a, c] = await Promise.all([listSiteBanners(), listSiteAnnouncements(), listConfigs()])
      setBanners(b)
      setAnnouncements(a)
      setConfigs(c)
      setError('')
    } catch (err) {
      setError(extractError(err, '加载系统设置失败（需要管理员权限）'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadSite()
  }, [loadSite])

  const loadQueries = useCallback(async (search: string, offset: number) => {
    try {
      const res = await listQueries({ limit: QUERY_PAGE, offset, search: search.trim() })
      setQueries(res.items)
      setQueryTotal(res.total)
      setQueryOffset(offset)
      setQueryError('')
    } catch (err) {
      setQueryError(extractError(err, '加载选品库失败'))
    }
  }, [])

  useEffect(() => {
    loadQueries('', 0)
  }, [loadQueries])

  // ── Banner ──

  function openBannerModal(item?: SiteBanner) {
    setBannerForm(
      item
        ? {
            image_url: item.image_url,
            link_url: item.link_url ?? '',
            title: item.title,
            sort_order: item.sort_order,
            enabled: item.enabled,
          }
        : { image_url: '', link_url: '', title: '', sort_order: 0, enabled: true },
    )
    setBannerFormError('')
    setBannerModal({ editing: item ?? null })
  }

  async function handleBannerSave(e: FormEvent) {
    e.preventDefault()
    if (!bannerModal) return
    if (!bannerForm.image_url.trim()) {
      setBannerFormError('图片 URL 不能为空')
      return
    }
    setBannerSaving(true)
    setBannerFormError('')
    try {
      if (bannerModal.editing) {
        await updateSiteBanner(bannerModal.editing.id, bannerForm)
      } else {
        await createSiteBanner(bannerForm)
      }
      setBannerModal(null)
      setBanners(await listSiteBanners())
    } catch (err) {
      setBannerFormError(extractError(err, '保存 Banner 失败'))
    } finally {
      setBannerSaving(false)
    }
  }

  async function handleBannerDelete(item: SiteBanner) {
    if (!window.confirm(`确认删除 Banner「${item.title || item.image_url}」？`)) return
    try {
      await deleteSiteBanner(item.id)
      setBanners(await listSiteBanners())
    } catch (err) {
      window.alert(extractError(err, '删除 Banner 失败'))
    }
  }

  // ── 通告 ──

  function openAnnouncementModal(item?: SiteAnnouncement) {
    setAnnouncementForm(
      item
        ? {
            title: item.title,
            content: item.content,
            announcement_type: item.announcement_type,
            enabled: item.enabled,
          }
        : { title: '', content: '', announcement_type: 'banner', enabled: true },
    )
    setAnnouncementFormError('')
    setAnnouncementModal({ editing: item ?? null })
  }

  async function handleAnnouncementSave(e: FormEvent) {
    e.preventDefault()
    if (!announcementModal) return
    if (!announcementForm.content.trim()) {
      setAnnouncementFormError('通告内容不能为空')
      return
    }
    setAnnouncementSaving(true)
    setAnnouncementFormError('')
    try {
      if (announcementModal.editing) {
        await updateSiteAnnouncement(announcementModal.editing.id, announcementForm)
      } else {
        await createSiteAnnouncement(announcementForm)
      }
      setAnnouncementModal(null)
      setAnnouncements(await listSiteAnnouncements())
    } catch (err) {
      setAnnouncementFormError(extractError(err, '保存通告失败'))
    } finally {
      setAnnouncementSaving(false)
    }
  }

  async function handleAnnouncementDelete(item: SiteAnnouncement) {
    if (!window.confirm(`确认删除通告「${item.title || item.content.slice(0, 20)}」？`)) return
    try {
      await deleteSiteAnnouncement(item.id)
      setAnnouncements(await listSiteAnnouncements())
    } catch (err) {
      window.alert(extractError(err, '删除通告失败'))
    }
  }

  // ── 引擎配置 ──

  async function loadBackups(name: string) {
    try {
      setBackups(await listConfigBackups(name))
    } catch {
      setBackups([])
    }
  }

  async function openConfig(name: string) {
    setActiveConfig(name)
    setConfigNotice('')
    setConfigError('')
    try {
      const data = await getConfig(name)
      setConfigContent(JSON.stringify(data, null, 2))
      await loadBackups(name)
    } catch (err) {
      setConfigError(extractError(err, '读取配置失败'))
    }
  }

  function validateConfig(): string {
    try {
      JSON.parse(configContent)
      return ''
    } catch (err) {
      return err instanceof Error ? err.message : 'JSON 解析失败'
    }
  }

  async function handleConfigSave() {
    if (!activeConfig) return
    const invalid = validateConfig()
    if (invalid) {
      setConfigError(`JSON 校验失败：${invalid}`)
      return
    }
    setConfigSaving(true)
    setConfigError('')
    setConfigNotice('')
    try {
      await putConfig(activeConfig, configContent)
      setConfigNotice('已保存（自动备份，保留 5 份）')
      await loadBackups(activeConfig)
    } catch (err) {
      setConfigError(extractError(err, '保存配置失败'))
    } finally {
      setConfigSaving(false)
    }
  }

  async function handleRollback() {
    if (!activeConfig || !rollbackTarget) return
    setRollbackBusy(true)
    setConfigError('')
    try {
      await rollbackConfig(activeConfig, rollbackTarget.name)
      const data = await getConfig(activeConfig)
      setConfigContent(JSON.stringify(data, null, 2))
      setConfigNotice(`已回滚到备份「${rollbackTarget.name}」`)
      setRollbackTarget(null)
      await loadBackups(activeConfig)
    } catch (err) {
      setConfigError(extractError(err, '回滚失败'))
    } finally {
      setRollbackBusy(false)
    }
  }

  // ── 选品库 ──

  function handleQuerySearch() {
    loadQueries(querySearch, 0)
  }

  async function handleQueryDelete(id: number) {
    if (!window.confirm('确认删除该关键词？')) return
    setQueryDeleting(id)
    try {
      await deleteQuery(id)
      await loadQueries(querySearch, queryOffset)
    } catch (err) {
      setQueryError(extractError(err, '删除关键词失败'))
    } finally {
      setQueryDeleting(null)
    }
  }

  function handleImportFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      setImportCsv(String(reader.result ?? ''))
    }
    reader.readAsText(file)
  }

  async function handleImport(e: FormEvent) {
    e.preventDefault()
    const csv = importCsv.trim()
    if (!csv) {
      setImportError('请输入 CSV 内容或选择文件')
      return
    }
    setImportBusy(true)
    setImportError('')
    setImportResult('')
    try {
      const res = await importQueries({ csv })
      const parts = [`新增 ${res.imported}`, `更新 ${res.updated}`]
      if (res.errors.length > 0) parts.push(`错误 ${res.errors.length} 条`)
      setImportResult(`导入完成：${parts.join('，')}`)
      setImportCsv('')
      setQuerySearch('')
      await loadQueries('', 0)
    } catch (err) {
      setImportError(extractError(err, '导入失败'))
    } finally {
      setImportBusy(false)
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <h1 className="page-title">系统设置</h1>
        <span className="page-badge">v0.55</span>
      </header>

      <div className="order-tabs">
        <button className={`order-tab${tab === 'site' ? ' active' : ''}`} onClick={() => setTab('site')}>
          站点运营
        </button>
        <button className={`order-tab${tab === 'config' ? ' active' : ''}`} onClick={() => setTab('config')}>
          引擎配置
        </button>
        <button className={`order-tab${tab === 'queries' ? ' active' : ''}`} onClick={() => setTab('queries')}>
          选品库
        </button>
        <button className={`order-tab${tab === 'business' ? ' active' : ''}`} onClick={() => setTab('business')}>
          商业
        </button>
      </div>

      {loading ? (
        <div className="card">
          <div className="empty-state">
            <div
              className="spinner"
              style={{ borderColor: 'rgba(0, 91, 255, 0.2)', borderTopColor: 'var(--color-brand)' }}
            />
            <p className="empty-state-text">加载系统设置…</p>
          </div>
        </div>
      ) : error ? (
        <div className="card">
          <div className="empty-state">
            <div className="form-error" role="alert">
              <span>{error}</span>
            </div>
            <button className="btn" onClick={() => loadSite()}>
              重试
            </button>
          </div>
        </div>
      ) : (
        <>
          {tab === 'site' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              <div className="card stores-table-wrap">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                  <div className="store-name">Banner（{banners.length}）</div>
                  <button type="button" className="btn btn-small" onClick={() => openBannerModal()}>
                    新增 Banner
                  </button>
                </div>
                {banners.length === 0 ? (
                  <p className="empty-state-text">暂无 Banner</p>
                ) : (
                  <table className="stores-table">
                    <thead>
                      <tr>
                        <th>图片</th>
                        <th>标题</th>
                        <th>链接</th>
                        <th>排序</th>
                        <th>状态</th>
                        <th className="col-actions">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {banners.map((b) => (
                        <tr key={b.id}>
                          <td>
                            <img
                              src={b.image_url}
                              alt={b.title || 'banner'}
                              style={{ width: 60, height: 36, objectFit: 'cover', borderRadius: 4, display: 'block' }}
                            />
                          </td>
                          <td>{b.title || '—'}</td>
                          <td className="mono" style={{ maxWidth: 260 }}>
                            {b.link_url || '—'}
                          </td>
                          <td>{b.sort_order}</td>
                          <td>
                            <EnabledBadge enabled={b.enabled} />
                          </td>
                          <td className="col-actions">
                            <button type="button" className="btn btn-small" onClick={() => openBannerModal(b)}>
                              编辑
                            </button>
                            <button type="button" className="btn btn-small btn-danger-text" onClick={() => handleBannerDelete(b)}>
                              删除
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              <div className="card stores-table-wrap">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                  <div className="store-name">通告（{announcements.length}）</div>
                  <button type="button" className="btn btn-small" onClick={() => openAnnouncementModal()}>
                    新增通告
                  </button>
                </div>
                {announcements.length === 0 ? (
                  <p className="empty-state-text">暂无通告</p>
                ) : (
                  <table className="stores-table">
                    <thead>
                      <tr>
                        <th>标题</th>
                        <th>内容</th>
                        <th>类型</th>
                        <th>状态</th>
                        <th>创建时间</th>
                        <th className="col-actions">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {announcements.map((a) => (
                        <tr key={a.id}>
                          <td>{a.title || '—'}</td>
                          <td style={{ maxWidth: 320 }}>{a.content}</td>
                          <td>
                            <span className={`badge ${a.announcement_type === 'popup' ? 'badge-warning' : 'badge-default'}`}>
                              {a.announcement_type}
                            </span>
                          </td>
                          <td>
                            <EnabledBadge enabled={a.enabled} />
                          </td>
                          <td className="col-time">{fmtTime(a.created_at)}</td>
                          <td className="col-actions">
                            <button type="button" className="btn btn-small" onClick={() => openAnnouncementModal(a)}>
                              编辑
                            </button>
                            <button type="button" className="btn btn-small btn-danger-text" onClick={() => handleAnnouncementDelete(a)}>
                              删除
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}

          {tab === 'config' && (
            <div style={{ display: 'flex', gap: '16px', alignItems: 'flex-start', flexWrap: 'wrap' }}>
              <div className="card" style={{ minWidth: '220px', flex: '0 0 220px' }}>
                <div className="store-name" style={{ marginBottom: 8 }}>
                  配置文件（{configs.length}）
                </div>
                {configs.map((c) => (
                  <button
                    key={c.name}
                    type="button"
                    className={`order-tab${activeConfig === c.name ? ' active' : ''}`}
                    style={{ display: 'block', width: '100%', textAlign: 'left', marginBottom: 4 }}
                    onClick={() => openConfig(c.name)}
                  >
                    {c.name}
                  </button>
                ))}
              </div>
              <div className="card" style={{ flex: '1 1 480px', minWidth: '320px' }}>
                {!activeConfig ? (
                  <p className="empty-state-text">点击左侧配置文件开始编辑</p>
                ) : (
                  <>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                      <div className="store-name mono">{activeConfig}</div>
                      <span className="store-meta">备份 {backups.length} 份</span>
                    </div>
                    {configError && (
                      <div className="form-error" role="alert">
                        {configError}
                      </div>
                    )}
                    {configNotice && (
                      <div className="key-manager-notice" role="status">
                        {configNotice}
                      </div>
                    )}
                    <textarea
                      className="field-input mono"
                      style={{ width: '100%', minHeight: 280, height: 'auto', resize: 'vertical', padding: 'var(--space-3)', fontSize: 'var(--text-sm)', whiteSpace: 'pre' }}
                      value={configContent}
                      onChange={(e) => setConfigContent(e.target.value)}
                      spellCheck={false}
                    />
                    <div className="modal-actions">
                      <button
                        type="button"
                        className="btn"
                        onClick={() => {
                          const invalid = validateConfig()
                          if (invalid) {
                            setConfigError(`JSON 校验失败：${invalid}`)
                          } else {
                            setConfigNotice('JSON 校验通过')
                          }
                        }}
                      >
                        校验
                      </button>
                      <button type="button" className="btn btn-primary" disabled={configSaving} onClick={handleConfigSave}>
                        {configSaving ? '保存中…' : '保存'}
                      </button>
                    </div>
                    <div className="order-detail-title">备份列表</div>
                    {backups.length === 0 ? (
                      <p className="empty-state-text">暂无备份</p>
                    ) : (
                      <table className="stores-table">
                        <thead>
                          <tr>
                            <th>备份文件</th>
                            <th>大小</th>
                            <th>时间</th>
                            <th className="col-actions">操作</th>
                          </tr>
                        </thead>
                        <tbody>
                          {backups.map((bk) => (
                            <tr key={bk.name}>
                              <td className="mono">{bk.name}</td>
                              <td>{(bk.size / 1024).toFixed(1)} KB</td>
                              <td className="col-time">{fmtTime(new Date(bk.mtime * 1000).toISOString())}</td>
                              <td className="col-actions">
                                <button type="button" className="btn btn-small" onClick={() => setRollbackTarget(bk)}>
                                  回滚
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </>
                )}
              </div>
            </div>
          )}

          {tab === 'queries' && (
            <div className="card stores-table-wrap">
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap', marginBottom: '12px' }}>
                <input
                  className="field-input"
                  style={{ width: 260 }}
                  type="text"
                  placeholder="搜索关键词…"
                  value={querySearch}
                  onChange={(e) => setQuerySearch(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleQuerySearch()
                  }}
                  spellCheck={false}
                />
                <button type="button" className="btn btn-small" onClick={handleQuerySearch}>
                  搜索
                </button>
                <button type="button" className="btn btn-small" onClick={() => setImportOpen(true)}>
                  CSV 导入
                </button>
                <span className="store-meta">共 {queryTotal} 条</span>
              </div>
              {queryError && (
                <div className="form-error" role="alert">
                  {queryError}
                </div>
              )}
              {queries.length === 0 ? (
                <p className="empty-state-text">暂无数据</p>
              ) : (
                <table className="stores-table">
                  <thead>
                    <tr>
                      <th>关键词</th>
                      <th>次数</th>
                      <th>ca</th>
                      <th>来源</th>
                      <th>创建时间</th>
                      <th className="col-actions">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {queries.map((q) => (
                      <tr key={q.id}>
                        <td>{q.query}</td>
                        <td>{q.count}</td>
                        <td>{q.ca != null ? q.ca : '—'}</td>
                        <td>
                          <span className="badge badge-currency">{q.source}</span>
                        </td>
                        <td className="col-time">{fmtTime(q.created_at)}</td>
                        <td className="col-actions">
                          <button
                            type="button"
                            className="btn btn-small btn-danger-text"
                            disabled={queryDeleting === q.id}
                            onClick={() => handleQueryDelete(q.id)}
                          >
                            {queryDeleting === q.id ? '删除中…' : '删除'}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <div className="modal-actions">
                <button
                  type="button"
                  className="btn btn-small"
                  disabled={queryOffset <= 0}
                  onClick={() => loadQueries(querySearch, Math.max(0, queryOffset - QUERY_PAGE))}
                >
                  上一页
                </button>
                <button
                  type="button"
                  className="btn btn-small"
                  disabled={queryOffset + QUERY_PAGE >= queryTotal}
                  onClick={() => loadQueries(querySearch, queryOffset + QUERY_PAGE)}
                >
                  下一页
                </button>
              </div>
            </div>
          )}

          {tab === 'business' && (
            <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
              <div
                className="card"
                role="button"
                tabIndex={0}
                style={{ padding: '20px', minWidth: '220px', cursor: 'pointer' }}
                onClick={() => navigate('/subscriptions')}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') navigate('/subscriptions')
                }}
              >
                <div className="store-name">订阅套餐管理</div>
                <div className="store-meta">查看与购买会员套餐</div>
              </div>
              <div
                className="card"
                role="button"
                tabIndex={0}
                style={{ padding: '20px', minWidth: '220px', cursor: 'pointer' }}
                onClick={() => navigate('/wallet')}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') navigate('/wallet')
                }}
              >
                <div className="store-name">充值</div>
                <div className="store-meta">账户余额充值</div>
              </div>
            </div>
          )}
        </>
      )}

      {bannerModal && (
        <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && setBannerModal(null)}>
          <div className="modal" role="dialog" aria-modal="true" aria-label="编辑 Banner">
            <div className="modal-header">
              <h2 className="modal-title">{bannerModal.editing ? '编辑 Banner' : '新增 Banner'}</h2>
              <button type="button" className="modal-close" aria-label="关闭" onClick={() => setBannerModal(null)}>
                ×
              </button>
            </div>
            <form className="modal-body modal-form" onSubmit={handleBannerSave}>
              {bannerFormError && (
                <div className="form-error" role="alert">
                  {bannerFormError}
                </div>
              )}
              <label className="field" htmlFor="banner-image-url">
                <span className="field-label">图片 URL</span>
                <input
                  id="banner-image-url"
                  className="field-input"
                  type="text"
                  value={bannerForm.image_url}
                  onChange={(e) => setBannerForm({ ...bannerForm, image_url: e.target.value })}
                  spellCheck={false}
                />
              </label>
              <label className="field" htmlFor="banner-link-url">
                <span className="field-label">跳转链接（可选）</span>
                <input
                  id="banner-link-url"
                  className="field-input"
                  type="text"
                  value={bannerForm.link_url ?? ''}
                  onChange={(e) => setBannerForm({ ...bannerForm, link_url: e.target.value })}
                  spellCheck={false}
                />
              </label>
              <label className="field" htmlFor="banner-title">
                <span className="field-label">标题</span>
                <input
                  id="banner-title"
                  className="field-input"
                  type="text"
                  value={bannerForm.title}
                  onChange={(e) => setBannerForm({ ...bannerForm, title: e.target.value })}
                  spellCheck={false}
                />
              </label>
              <label className="field" htmlFor="banner-sort">
                <span className="field-label">排序（数字越小越靠前）</span>
                <input
                  id="banner-sort"
                  className="field-input"
                  type="number"
                  value={bannerForm.sort_order}
                  onChange={(e) => setBannerForm({ ...bannerForm, sort_order: Number(e.target.value) || 0 })}
                />
              </label>
              <label className="field" htmlFor="banner-enabled">
                <span className="field-label">状态</span>
                <select
                  id="banner-enabled"
                  className="field-select"
                  value={bannerForm.enabled ? '1' : '0'}
                  onChange={(e) => setBannerForm({ ...bannerForm, enabled: e.target.value === '1' })}
                >
                  <option value="1">启用</option>
                  <option value="0">停用</option>
                </select>
              </label>
              <div className="modal-actions">
                <button type="button" className="btn" onClick={() => setBannerModal(null)}>
                  取消
                </button>
                <button type="submit" className="btn btn-primary" disabled={bannerSaving}>
                  {bannerSaving ? '保存中…' : '保存'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {announcementModal && (
        <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && setAnnouncementModal(null)}>
          <div className="modal" role="dialog" aria-modal="true" aria-label="编辑通告">
            <div className="modal-header">
              <h2 className="modal-title">{announcementModal.editing ? '编辑通告' : '新增通告'}</h2>
              <button
                type="button"
                className="modal-close"
                aria-label="关闭"
                onClick={() => setAnnouncementModal(null)}
              >
                ×
              </button>
            </div>
            <form className="modal-body modal-form" onSubmit={handleAnnouncementSave}>
              {announcementFormError && (
                <div className="form-error" role="alert">
                  {announcementFormError}
                </div>
              )}
              <label className="field" htmlFor="announcement-title">
                <span className="field-label">标题</span>
                <input
                  id="announcement-title"
                  className="field-input"
                  type="text"
                  value={announcementForm.title}
                  onChange={(e) => setAnnouncementForm({ ...announcementForm, title: e.target.value })}
                  spellCheck={false}
                />
              </label>
              <label className="field" htmlFor="announcement-content">
                <span className="field-label">内容</span>
                <textarea
                  id="announcement-content"
                  className="field-input"
                  rows={4}
                  value={announcementForm.content}
                  onChange={(e) => setAnnouncementForm({ ...announcementForm, content: e.target.value })}
                  spellCheck={false}
                />
              </label>
              <label className="field" htmlFor="announcement-type">
                <span className="field-label">类型</span>
                <select
                  id="announcement-type"
                  className="field-select"
                  value={announcementForm.announcement_type}
                  onChange={(e) => setAnnouncementForm({ ...announcementForm, announcement_type: e.target.value })}
                >
                  <option value="banner">banner（横幅）</option>
                  <option value="popup">popup（弹窗）</option>
                </select>
              </label>
              <label className="field" htmlFor="announcement-enabled">
                <span className="field-label">状态</span>
                <select
                  id="announcement-enabled"
                  className="field-select"
                  value={announcementForm.enabled ? '1' : '0'}
                  onChange={(e) => setAnnouncementForm({ ...announcementForm, enabled: e.target.value === '1' })}
                >
                  <option value="1">启用</option>
                  <option value="0">停用</option>
                </select>
              </label>
              <div className="modal-actions">
                <button type="button" className="btn" onClick={() => setAnnouncementModal(null)}>
                  取消
                </button>
                <button type="submit" className="btn btn-primary" disabled={announcementSaving}>
                  {announcementSaving ? '保存中…' : '保存'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {importOpen && (
        <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && setImportOpen(false)}>
          <div className="modal" role="dialog" aria-modal="true" aria-label="CSV 导入关键词">
            <div className="modal-header">
              <h2 className="modal-title">CSV 导入关键词</h2>
              <button type="button" className="modal-close" aria-label="关闭" onClick={() => setImportOpen(false)}>
                ×
              </button>
            </div>
            <form className="modal-body modal-form" onSubmit={handleImport}>
              {importError && (
                <div className="form-error" role="alert">
                  {importError}
                </div>
              )}
              {importResult && (
                <div className="key-manager-notice" role="status">
                  {importResult}
                </div>
              )}
              <label className="field" htmlFor="import-file">
                <span className="field-label">选择 CSV 文件</span>
                <input id="import-file" className="field-input" type="file" accept=".csv,text/csv" onChange={handleImportFile} />
              </label>
              <label className="field" htmlFor="import-csv-text">
                <span className="field-label">或直接粘贴 CSV 内容</span>
                <textarea
                  id="import-csv-text"
                  className="field-input"
                  rows={6}
                  value={importCsv}
                  onChange={(e) => setImportCsv(e.target.value)}
                  placeholder={'query,count,ca\n示例关键词,120,3.5'}
                  spellCheck={false}
                />
              </label>
              <div className="modal-actions">
                <button type="button" className="btn" onClick={() => setImportOpen(false)}>
                  取消
                </button>
                <button type="submit" className="btn btn-primary" disabled={importBusy}>
                  {importBusy ? '导入中…' : '导入'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {rollbackTarget && (
        <div className="modal-mask" role="dialog" aria-modal="true" aria-label="确认回滚">
          <div className="modal">
            <h3 className="modal-title">确认回滚</h3>
            <p className="modal-text">
              将把 {activeConfig} 回滚到备份「{rollbackTarget.name}」，当前内容会被覆盖（回滚不产生新备份）。确认继续？
            </p>
            <div className="modal-actions">
              <button type="button" className="btn" onClick={() => setRollbackTarget(null)}>
                取消
              </button>
              <button type="button" className="btn btn-danger" disabled={rollbackBusy} onClick={handleRollback}>
                {rollbackBusy ? '回滚中…' : '确认回滚'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
