/*
Copyright (C) 2023-2026 QuantumNous — GNU AGPL v3
Referral Withdrawal Modal
*/
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { toast } from 'sonner'
import { getCommonHeaders } from '@/lib/api'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  maxAmount: number
  onSuccess: () => void
}

export function ReferralWithdrawalModal({ open, onOpenChange, maxAmount, onSuccess }: Props) {
  const { t } = useTranslation()
  const [amount, setAmount] = useState('')
  const [accountType, setAccountType] = useState('alipay')
  const [accountName, setAccountName] = useState('')
  const [accountNo, setAccountNo] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async () => {
    if (!amount || Number(amount) <= 0) { toast.error('请输入提现金额'); return }
    if (!accountName) { toast.error('请输入收款人姓名'); return }
    if (!accountNo) { toast.error('请输入收款账号'); return }
    setLoading(true)
    try {
      const res = await fetch('/api/user/referral/withdrawals', {
        method: 'POST',
        headers: { ...getCommonHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount: Number(amount), account_type: accountType, account_name: accountName, account_no: accountNo }),
      })
      const data = await res.json()
      if (data.success) { toast.success('提现申请已提交'); onSuccess() }
      else { toast.error(data.message || '提现失败') }
    } catch { toast.error('网络错误') }
    finally { setLoading(false) }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-[420px]'>
        <DialogHeader>
          <DialogTitle>申请提现</DialogTitle>
          <DialogDescription>可提现余额：¥{maxAmount.toFixed(2)}</DialogDescription>
        </DialogHeader>
        <div className='space-y-4 pt-2'>
          <div className='space-y-2'>
            <Label>提现金额</Label>
            <Input type='number' placeholder='请输入提现金额' value={amount} onChange={e => setAmount(e.target.value)} max={maxAmount} />
          </div>
          <div className='space-y-2'>
            <Label>收款方式</Label>
            <Select value={accountType} onValueChange={(v) => v !== null && setAccountType(v)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value='alipay'>支付宝</SelectItem>
                <SelectItem value='wechat'>微信</SelectItem>
                <SelectItem value='bank'>银行卡</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className='space-y-2'>
            <Label>收款人姓名</Label>
            <Input placeholder='请输入收款人姓名' value={accountName} onChange={e => setAccountName(e.target.value)} />
          </div>
          <div className='space-y-2'>
            <Label>收款账号</Label>
            <Input placeholder='请输入收款账号' value={accountNo} onChange={e => setAccountNo(e.target.value)} />
          </div>
          <Button className='w-full' onClick={handleSubmit} disabled={loading}>{loading ? '提交中...' : '提交提现申请'}</Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
