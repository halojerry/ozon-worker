import { createFileRoute } from '@tanstack/react-router'
import { default as Page } from '@/pages/OnSale'

export const Route = createFileRoute('/_authenticated/on-sale/')({
  component: Page,
})
