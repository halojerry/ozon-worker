import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { getStoredToken } from '@/api/client'

/**
 * 工作台连接横幅：业务 API 需要 sk-token（worker /api/v1 鉴权）。
 * mxou 登录（cookie 会话）≠ worker token，未配置时在顶部提示去 API Keys 创建。
 */
export function WorkbenchConnectBanner() {
  const { t } = useTranslation()
  if (getStoredToken()) return null
  return (
    <div className='border-border bg-muted/50 text-foreground/80 flex items-center justify-between gap-4 border-b px-6 py-2 text-sm'>
      <span>
        {t('尚未连接 Ozon 工作台：请创建或选择 API Key 以启用商品/订单/任务功能')}
      </span>
      <Link to='/keys' className='hover:text-primary font-medium underline underline-offset-4'>
        {t('去 API Keys 连接')}
      </Link>
    </div>
  )
}
