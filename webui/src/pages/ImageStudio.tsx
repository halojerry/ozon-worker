import { useSearchParams } from 'react-router-dom'
import ImageStudioEmbed from '../components/ImageStudioEmbed'

/* ════════════════════════════════════════════════════════════
 * T13 生图工作台（AI 商品套图规格，W4 移除积分）
 *
 * 入口：/image-studio?taskId=xxx（任务进度页「生图」）
 *        /image-studio?draftId=xxx（商品编辑页「AI商品套图」）
 *
 * T11 重构：核心生图交互（原图/卖点/图配置/一键生成/预览/对比）
 * 已抽入 components/ImageStudioEmbed.tsx（props 驱动，可被编辑页
 * 复用）。本页面只保留路由级职责：读 searchParams、页面标题/徽标/
 * 任务标识、无上下文空态，其余原样委托给 Embed。
 * ════════════════════════════════════════════════════════════ */

export default function ImageStudio() {
  const [params] = useSearchParams()
  const taskId = params.get('taskId') ?? ''
  const draftId = params.get('draftId') ?? ''
  const identityId = taskId || draftId

  return (
    <div className="page">
      <header className="page-header">
        <h1 className="page-title">生图工作台</h1>
        <span className="page-badge">T13</span>
        {identityId && (
          <span className="studio-task-meta mono">
            {taskId ? `任务 ${taskId.slice(0, 8)}` : `草稿 ${draftId.slice(0, 8)}`}
          </span>
        )}
      </header>

      {!identityId ? (
        <div className="card">
          <div className="empty-state">
            <div className="placeholder-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="1.6">
                <rect x="3.5" y="3.5" width="17" height="17" rx="2.5" />
                <circle cx="9" cy="9" r="1.8" />
                <path d="M4.5 18.5l5-5 3.5 3.5 3-3 3.5 3.5" />
              </svg>
            </div>
            <p className="empty-state-title">未指定任务或草稿</p>
            <p className="empty-state-text">请从「任务进度」页点击行内「生图」，或从「商品编辑」页点击「AI商品套图」进入</p>
          </div>
        </div>
      ) : (
        <ImageStudioEmbed
          mode={taskId ? 'task' : 'draft'}
          taskId={taskId || undefined}
          draftId={draftId || undefined}
        />
      )}
    </div>
  )
}
