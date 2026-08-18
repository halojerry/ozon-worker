import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import type { components } from '@/api/generated'

type CredentialOut = components['schemas']['CredentialOut']

export interface DefaultCredentialResult {
  credential: CredentialOut | null
  isLoading: boolean
}

export function useDefaultCredential(): DefaultCredentialResult {
  const { data, isLoading } = useQuery<CredentialOut[]>({
    queryKey: ['credentials-default'],
    queryFn: async () => {
      const { data } = await api.get<CredentialOut[]>('/credentials')
      return Array.isArray(data) ? data : []
    },
    staleTime: 60_000,
  })

  const list = data ?? []
  const credential =
    list.find((c) => c.is_default) ?? list.find((c) => c.status === 'active') ?? list[0] ?? null

  return { credential, isLoading }
}

export function NoStoreHint() {
  return (
    <div className="flex flex-col items-center gap-3 rounded-card border border-dashed border-line bg-surface px-6 py-10 text-center">
      <p className="text-[13px] text-ink-3">未配置默认店铺，无法加载数据</p>
      <a
        href="/app/stores"
        className="rounded-input bg-accent px-4 py-1.5 text-[13px] font-medium text-white hover:bg-accent-dark"
      >
        前往店铺管理添加
      </a>
    </div>
  )
}
