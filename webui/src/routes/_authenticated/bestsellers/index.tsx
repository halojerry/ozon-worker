import { createFileRoute } from '@tanstack/react-router'
import { default as Page } from '@/pages/Bestsellers'

export const Route = createFileRoute('/_authenticated/bestsellers/')({
  component: Page,
})
