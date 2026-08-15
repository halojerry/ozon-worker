import { Navigate } from 'react-router-dom'
import PagePlaceholder from '../components/PagePlaceholder'

/** 空壳首页：T4 阶段无业务内容，重定向到采集箱（首个业务入口） */
export default function Home() {
  return <Navigate to="/collect-box" replace />
}

/** 首页占位（保留备用，避免 / 路由 404 兜底） */
export function HomePlaceholder() {
  return (
    <PagePlaceholder
      title="工作台"
      description="Ozon 上架助手 WebUI 首页"
      taskRef="T4"
    />
  )
}
