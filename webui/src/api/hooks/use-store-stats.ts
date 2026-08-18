import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

interface StoreStats {
  credential_id: string
  ozon_client_id: string
  stats_date: string
  today_orders: number
  today_sales_amount: number
  today_commission: number
  today_profit: number
  today_product_count: number
}

export function useStoreStats(credentialId: string) {
  return useQuery<StoreStats>({
    queryKey: ['store-stats', credentialId],
    queryFn: async () => {
      const { data } = await api.get<StoreStats>(`/stores/${credentialId}/stats`)
      return data
    },
    enabled: !!credentialId,
  })
}
