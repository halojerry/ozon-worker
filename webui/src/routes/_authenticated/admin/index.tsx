import { createFileRoute, redirect } from '@tanstack/react-router'
import { useAuthStore } from '@/stores/auth-store'
import { ROLE } from '@/lib/roles'
import { default as Page } from '@/pages/Admin'

export const Route = createFileRoute('/_authenticated/admin/')({
  component: Page,
  beforeLoad: () => {
    const { auth } = useAuthStore.getState()
    const role = auth.user?.role ?? 0
    if (role < ROLE.ADMIN) {
      throw redirect({ to: '/403' })
    }
  },
})
