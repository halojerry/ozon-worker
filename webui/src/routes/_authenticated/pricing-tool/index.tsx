import { createFileRoute } from '@tanstack/react-router'
import { default as Page } from '@/pages/PricingTool'

export const Route = createFileRoute('/_authenticated/pricing-tool/')({
  component: Page,
})
