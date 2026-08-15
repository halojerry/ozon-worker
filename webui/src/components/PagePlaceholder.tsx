interface PagePlaceholderProps {
  title: string
  description: string
  /** 对应计划中的任务编号，如 T10 采集箱 */
  taskRef?: string
}

/**
 * 空壳页面占位（T4 脚手架阶段）。
 * 后续 T10-T13 各任务用真实页面替换 components/PagePlaceholder 的渲染。
 */
export default function PagePlaceholder({ title, description, taskRef }: PagePlaceholderProps) {
  return (
    <div className="page">
      <header className="page-header">
        <h1 className="page-title">{title}</h1>
        {taskRef && <span className="page-badge">{taskRef}</span>}
      </header>

      <div className="card placeholder-card">
        <div className="placeholder-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="1.6">
            <rect x="3.5" y="3.5" width="17" height="17" rx="2.5" />
            <path d="M8.5 8.5h3M8.5 12h7M8.5 15.5h5" />
          </svg>
        </div>
        <h2 className="placeholder-title">{description}</h2>
        <p className="placeholder-text">
          页面骨架已就绪，业务功能在后续迭代中实现（见 docs/PLAN-webui-v1.md 任务详情）。
        </p>
      </div>
    </div>
  )
}
