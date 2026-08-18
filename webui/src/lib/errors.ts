import { AxiosError } from 'axios'

/**
 * 错误提取：统一错误体 `{"detail": "错误信息"}`（FastAPI 标准，见 API-INTEGRATION-GUIDE §7）。
 * 兼容 AxiosError / string / 未知结构。
 */
export function getApiError(error: unknown): string {
  if (error instanceof AxiosError) {
    const data = error.response?.data as unknown
    if (data && typeof data === 'object' && 'detail' in data) {
      const detail = (data as { detail: unknown }).detail
      if (typeof detail === 'string') return detail
      if (Array.isArray(detail)) {
        // FastAPI 422 校验错误数组
        return detail.map((item) => (item && typeof item === 'object' && 'msg' in item ? String(item.msg) : '')).join('；')
      }
      return JSON.stringify(detail)
    }
    if (error.code === 'ERR_NETWORK') return '网络连接失败，请确认服务可用'
    if (error.code === 'ECONNABORTED') return '请求超时，请稍后重试'
    return error.message
  }
  if (typeof error === 'string') return error
  if (error instanceof Error) return error.message
  return '发生未知错误'
}

/** 把任意错误转成可展示的 message（带兜底） */
export function toErrorMessage(error: unknown, fallback = '操作失败，请重试'): string {
  const msg = getApiError(error)
  return msg || fallback
}
