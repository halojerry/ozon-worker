import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import type { components } from '@/api/generated'

type TaskStatisticsResponse = components['schemas']['TaskStatisticsResponse']

export function useTaskStatistics() {
  return useQuery<TaskStatisticsResponse>({
    queryKey: ['task-statistics'],
    queryFn: async () => {
      const { data } = await api.get<TaskStatisticsResponse>('/task_statistics')
      return data
    },
    refetchInterval: 30_000,
  })
}
