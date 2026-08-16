import { createFileRoute } from '@tanstack/react-router'
import { default as Page } from '@/pages/Admin'

export const Route = createFileRoute('/_authenticated/admin/')({
  component: Page,
})
