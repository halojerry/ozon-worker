import { createFileRoute } from '@tanstack/react-router'
import { default as Page } from '@/pages/DataScreen'

export const Route = createFileRoute('/_authenticated/data-screen/')({
  component: Page,
})
