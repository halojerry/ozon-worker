import { createFileRoute } from '@tanstack/react-router'
import { default as Page } from '@/pages/CollectBox'

export const Route = createFileRoute('/_authenticated/collect-box/')({
  component: Page,
})
