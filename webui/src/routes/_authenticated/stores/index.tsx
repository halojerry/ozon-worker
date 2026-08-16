import { createFileRoute } from '@tanstack/react-router'
import { default as Page } from '@/pages/Stores'

export const Route = createFileRoute('/_authenticated/stores/')({
  component: Page,
})
