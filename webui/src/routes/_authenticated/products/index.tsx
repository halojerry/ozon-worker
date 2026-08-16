import { createFileRoute } from '@tanstack/react-router'
import { default as Page } from '@/pages/Products'

export const Route = createFileRoute('/_authenticated/products/')({
  component: Page,
})
