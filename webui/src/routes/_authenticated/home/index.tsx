import { createFileRoute } from '@tanstack/react-router'
import { default as Page } from '@/pages/Home'

export const Route = createFileRoute('/_authenticated/home/')({
  component: Page,
})
