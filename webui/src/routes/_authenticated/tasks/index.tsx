import { createFileRoute } from '@tanstack/react-router'
import { default as Page } from '@/pages/Tasks'

export const Route = createFileRoute('/_authenticated/tasks/')({
  component: Page,
})
