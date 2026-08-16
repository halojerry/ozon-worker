import {
  Box,
  CreditCard,
  FileText,
  Image,
  LayoutDashboard,
  ListTodo,
  Package,
  Settings,
  ShoppingBag,
  Store,
  Tags,
  TrendingUp,
  Truck,
  User,
  Users,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { type SidebarData } from '@/components/layout/types'

export function useSidebarData(): SidebarData {
  const { t } = useTranslation()
  return {
    navGroups: [
      {
        id: 'workbench',
        title: t('工作台'),
        items: [
          { title: t('首页'), url: '/', icon: LayoutDashboard },
          { title: t('采集箱'), url: '/collect-box', icon: Package },
          { title: t('商品编辑'), url: '/products', icon: Box },
          { title: t('上架任务'), url: '/tasks', icon: ListTodo },
          { title: t('在线商品'), url: '/on-sale', icon: ShoppingBag },
        ],
      },
      {
        id: 'operations',
        title: t('运营'),
        items: [
          { title: t('订单管理'), url: '/orders', icon: Truck },
          { title: t('榜单选品'), url: '/bestsellers', icon: TrendingUp },
          { title: t('定价工具'), url: '/pricing-tool', icon: CreditCard },
          { title: t('数据大屏'), url: '/data-screen', icon: FileText },
          { title: t('生图工作台'), url: '/image-studio', icon: Image },
        ],
      },
      {
        id: 'config',
        title: t('配置'),
        items: [
          { title: t('店铺管理'), url: '/stores', icon: Store },
          { title: t('上架模板'), url: '/templates', icon: Tags },
          { title: t('个人中心'), url: '/profile', icon: User },
        ],
      },
      {
        id: 'admin',
        title: t('管理员'),
        items: [
          { title: t('管理后台'), url: '/admin', icon: Users },
          { title: t('系统设置'), url: '/system-settings', activeUrls: ['/system-settings'], icon: Settings },
        ],
      },
    ],
  }
}
