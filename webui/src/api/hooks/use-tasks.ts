import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import type { components } from '@/api/generated'

type TaskListResponse = components['schemas']['TaskListResponse']

export function useTasks(params?: { limit?: number; offset?: number; status?: string }) {
  return useQuery<TaskListResponse>({
    queryKey: ['tasks', params],
    queryFn: async () => {
      const { data } = await api.get<TaskListResponse>('/tasks', { params })
      return data
    },
  })
}
