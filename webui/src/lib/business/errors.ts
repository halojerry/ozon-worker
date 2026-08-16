/**
 * 业务页共享错误处理（S2.2 抽取：10 个页面原各自实现 extractError）
 *
 * 统一 axios 错误提取：优先 response.data.detail，其次 err.message，最后 fallback。
 * 调用方不得在页面内重新实现（避免行为漂移）。
 */
export function extractError(err: unknown, fallback: string): string {
  const e = err as { response?: { data?: { detail?: string } }; message?: string } | null
  return e?.response?.data?.detail ?? e?.message ?? fallback
}
