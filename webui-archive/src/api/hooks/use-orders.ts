import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import type { components } from '@/api/generated'

type OrderListResponse = components['schemas']['OrderListResponse']

export function useOrders(params?: { limit?: number; offset?: number; status?: string }) {
  return useQuery<OrderListResponse>({
    queryKey: ['orders', params],
    queryFn: async () => {
      const { data } = await api.get<OrderListResponse>('/orders', { params })
      return data
    },
  })
}
