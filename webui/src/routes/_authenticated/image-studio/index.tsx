import { createFileRoute } from '@tanstack/react-router'
import { default as Page } from '@/pages/ImageStudio'

export const Route = createFileRoute('/_authenticated/image-studio/')({
  component: Page,
})
