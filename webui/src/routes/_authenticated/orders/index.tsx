import { createFileRoute } from '@tanstack/react-router'
import { default as Page } from '@/pages/Orders'

export const Route = createFileRoute('/_authenticated/orders/')({
  component: Page,
})
