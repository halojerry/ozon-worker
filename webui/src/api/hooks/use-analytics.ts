import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

interface BestsellerItem {
  sku_or_id: string
  brand: string
  category_path: string
  ordering_amount: number
  ordering_count: number
  avg_price_rub: number
}

export function useBestsellers(params?: { limit?: number; offset?: number; category?: string }) {
  return useQuery<BestsellerItem[]>({
    queryKey: ['bestsellers', params],
    queryFn: async () => {
      const { data } = await api.get('/analytics/bestsellers', { params })
      return Array.isArray(data) ? data : []
    },
  })
}
