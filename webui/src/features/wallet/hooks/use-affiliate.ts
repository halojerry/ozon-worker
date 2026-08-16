/*
Copyright (C) 2023-2026 QuantumNous

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.

For commercial licensing, please contact support@quantumnous.com
*/
import { useState, useEffect, useCallback } from 'react'
import i18next from 'i18next'
import { toast } from 'sonner'
import { getSelf } from '@/lib/api'
import { useCopyToClipboard } from '@/hooks/use-copy-to-clipboard'
import { getAffiliateCode, transferAffiliateQuota } from '../api'
import { getCommonHeaders } from '@/lib/api'
import { generateAffiliateLink } from '../lib'
import type { ReferralSummary, ReferralInvite, ReferralCommission, ReferralWithdrawal, ReferralTreeSummary, ReferralTreeRow } from '../types'

const API = {
  summary: () => fetch('/api/user/referral/summary', { headers: getCommonHeaders() }).then(r => r.json()),
  invites: (depth = 0) => fetch(`/api/user/referral/invites?p=1&page_size=20&depth=${depth}`, { headers: getCommonHeaders() }).then(r => r.json()),
  commissions: () => fetch('/api/user/referral/commissions?p=1&page_size=20', { headers: getCommonHeaders() }).then(r => r.json()),
  withdrawals: () => fetch('/api/user/referral/withdrawals?p=1&page_size=20', { headers: getCommonHeaders() }).then(r => r.json()),
  tree: (userId: number) => fetch(`/api/user/${userId}/referral/tree`, { headers: getCommonHeaders() }).then(r => r.json()),
}

// ============================================================================
// Affiliate Hook
// ============================================================================

export function useAffiliate() {
  const [affiliateCode, setAffiliateCode] = useState<string>('')
  const [affiliateLink, setAffiliateLink] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [transferring, setTransferring] = useState(false)
  const { copyToClipboard } = useCopyToClipboard()

  // Referral data
  const [referralSummary, setReferralSummary] = useState<ReferralSummary>({})
  const [referralInvites, setReferralInvites] = useState<ReferralInvite[]>([])
  const [referralCommissions, setReferralCommissions] = useState<ReferralCommission[]>([])
  const [referralWithdrawals, setReferralWithdrawals] = useState<ReferralWithdrawal[]>([])
  const [referralTreeRows, setReferralTreeRows] = useState<ReferralTreeRow[]>([])
  const [referralTreeSummary, setReferralTreeSummary] = useState<ReferralTreeSummary | null>(null)
  const [referralInviteDepth, setReferralInviteDepth] = useState(0)
  const [userId, setUserId] = useState<number>(0)

  // Fetch referral data
  const fetchReferralData = useCallback(async (uid: number, depth: number) => {
    try {
      const [sumRes, invRes, comRes, wdRes] = await Promise.all([
        API.summary(), API.invites(depth), API.commissions(), API.withdrawals(),
      ])
      if (sumRes.success) { setReferralSummary(sumRes.data || {}); setUserId(uid) }
      if (invRes.success) {
        const inv = invRes.data
        setReferralInvites(Array.isArray(inv) ? inv : (inv?.items || []))
      }
      if (comRes.success) {
        const com = comRes.data
        setReferralCommissions(Array.isArray(com) ? com : (com?.items || []))
      }
      if (wdRes.success) {
        const wd = wdRes.data
        setReferralWithdrawals(Array.isArray(wd) ? wd : (wd?.items || []))
      }
      if (uid && sumRes.data?.can_view_commission_detail) {
        const treeRes = await API.tree(uid)
        if (treeRes.success) {
          const treeData = treeRes.data
          setReferralTreeRows(treeData?.items || treeData?.rows || [])
          setReferralTreeSummary(treeData?.summary || null)
        }
      }
    } catch (e) {
      console.error('Failed to fetch referral data:', e)
    }
  }, [])

  const refreshReferral = useCallback(() => {
    if (userId) fetchReferralData(userId, referralInviteDepth)
  }, [userId, referralInviteDepth, fetchReferralData])

  // Fetch affiliate code (existing + trigger referral fetch)
  const fetchAffiliateCode = useCallback(async () => {
    try {
      setLoading(true)
      const response = await getAffiliateCode()
      if (response.success && response.data) {
        setAffiliateCode(response.data)
        setAffiliateLink(generateAffiliateLink(response.data))
      }
      // Also get self for user ID
      const self = await getSelf()
      const uid = self?.data?.id || self?.id || 0
      if (uid) fetchReferralData(uid, 0)
    } catch (error) {
      console.error('Failed to fetch affiliate code:', error)
    } finally {
      setLoading(false)
    }
  }, [fetchReferralData])

  // Re-fetch when depth changes
  useEffect(() => {
    if (userId) {
      fetch(`/api/user/referral/invites?p=1&page_size=20&depth=${referralInviteDepth}`, { headers: getCommonHeaders() })
        .then(r => r.json())
        .then(d => { if (d.success) setReferralInvites(d.data || []) })
        .catch(() => {})
    }
  }, [referralInviteDepth, userId])

  // Copy affiliate link
  const copyAffiliateLink = useCallback(() => {
    copyToClipboard(affiliateLink)
  }, [affiliateLink, copyToClipboard])

  // Transfer affiliate quota to balance
  const transferQuota = useCallback(async (quota: number): Promise<boolean> => {
    try {
      setTransferring(true)
      const response = await transferAffiliateQuota({ quota })

      if (response.success) {
        toast.success(response.message || i18next.t('Transfer successful'))
        await getSelf()
        return true
      }

      toast.error(response.message || i18next.t('Transfer failed'))
      return false
    } catch (_error) {
      toast.error(i18next.t('Transfer failed'))
      return false
    } finally {
      setTransferring(false)
    }
  }, [])

  useEffect(() => {
    fetchAffiliateCode()
  }, [fetchAffiliateCode])

  return {
    affiliateCode, affiliateLink, loading, transferring, copyAffiliateLink, transferQuota, refetch: fetchAffiliateCode,
    // Referral data
    referralSummary, referralInvites, referralCommissions, referralWithdrawals,
    referralTreeRows, referralTreeSummary, referralInviteDepth, setReferralInviteDepth,
    refreshReferral,
  }
}
