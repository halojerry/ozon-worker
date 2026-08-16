import { createFileRoute } from '@tanstack/react-router'
import { default as Page } from '@/pages/Templates'

export const Route = createFileRoute('/_authenticated/templates/')({
  component: Page,
})
