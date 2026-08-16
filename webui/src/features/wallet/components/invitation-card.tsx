/*
Copyright (C) 2023-2026 QuantumNous — GNU AGPL v3
Referral Invitation Card — ported from classic theme with POUNDING styling
*/
import { useState, useMemo } from 'react'
import { Copy, Gift, Network, TrendingUp, Users, Wallet, CircleDollarSign } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { toast } from 'sonner'
import { ReferralWithdrawalModal } from './referral-withdrawal-modal'
import type { ReferralSummary, ReferralInvite, ReferralCommission, ReferralWithdrawal, ReferralTreeSummary, ReferralTreeRow } from '../types'

interface Props {
  affLink: string
  complianceConfirmed?: boolean
  referralSummary: ReferralSummary
  referralInvites: ReferralInvite[]
  referralCommissions: ReferralCommission[]
  referralWithdrawals: ReferralWithdrawal[]
  referralTreeRows: ReferralTreeRow[]
  referralTreeSummary: ReferralTreeSummary | null
  referralInviteDepth: number
  setReferralInviteDepth: (d: number) => void
  refreshReferral: () => void
  onOpenTransfer: () => void
  affQuota: number
  renderQuota: (q: number) => string
}

function fmtMoney(v: unknown) { return `¥${Number(v || 0).toFixed(2)}` }
function fmtRatio(v: unknown) { const n = Number(v || 0); return `${(n > 1 ? n : n * 100).toFixed(0)}%` }
function ts2str(ts: unknown) { if (!ts) return '-'; return new Date(Number(ts) * 1000).toLocaleString('zh-CN') }

function EmptyState({ msg, hint }: { msg: string; hint: string }) {
  return (
    <div className='py-8 text-center text-sm text-muted-foreground'>
      <Gift size={24} className='mx-auto mb-2 opacity-30' />
      <p>{msg}</p>
      <p className='text-xs mt-1'>{hint}</p>
    </div>
  )
}

export function InvitationCard({
  affLink, complianceConfirmed = true, referralSummary, referralInvites, referralCommissions,
  referralWithdrawals, referralTreeRows, referralTreeSummary, referralInviteDepth,
  setReferralInviteDepth, refreshReferral, onOpenTransfer, affQuota, renderQuota,
}: Props) {
  const [withdrawOpen, setWithdrawOpen] = useState(false)
  const canView = referralSummary?.can_view_commission_detail === true

  const overviewStats = useMemo(() => [
    { label: '累计邀请人数', value: referralSummary?.direct_invite_count || referralSummary?.aff_count || 0, icon: <Users size={14} /> },
    { label: '我的全网下级', value: referralSummary?.network_invite_count || 0, icon: <Network size={14} /> },
    { label: canView ? '直推奖励累计' : '赠送额度累计', value: canView ? fmtMoney(referralSummary?.commission_direct_total) : renderQuota(referralSummary?.aff_history_quota || affQuota || 0), icon: canView ? <TrendingUp size={14} /> : <Gift size={14} /> },
    { label: canView ? '累计返佣金额' : '可用赠送额度', value: canView ? fmtMoney(referralSummary?.commission_total) : renderQuota(referralSummary?.aff_quota || affQuota), icon: canView ? <CircleDollarSign size={14} /> : <Wallet size={14} /> },
  ], [canView, referralSummary, affQuota, renderQuota])

  const commissionStats = useMemo(() => canView ? [
    { label: '直推贡献流水', value: fmtMoney(referralSummary?.direct_contribution) },
    { label: '间推贡献流水', value: fmtMoney(referralSummary?.indirect_contribution) },
    { label: '全网贡献流水', value: fmtMoney(referralSummary?.network_contribution) },
    { label: '可提现余额', value: fmtMoney(referralSummary?.commission_balance) },
    { label: '审核中金额', value: fmtMoney(referralSummary?.commission_frozen) },
    { label: '已提现金额', value: fmtMoney(referralSummary?.commission_withdrawn) },
    { label: '一级下级人数', value: referralSummary?.level1_children_count || 0 },
    { label: '二级下级人数', value: referralSummary?.level2_children_count || 0 },
    { label: '三级到N级人数', value: referralSummary?.level3_and_beyond_count || 0 },
  ] : [], [canView, referralSummary])

  const copyLink = () => { navigator.clipboard.writeText(affLink); toast.success('已复制邀请链接') }

  return (
    <div className='space-y-6'>
      {canView && (
        <ReferralWithdrawalModal open={withdrawOpen} onOpenChange={setWithdrawOpen}
          maxAmount={referralSummary?.commission_balance || 0} onSuccess={() => { setWithdrawOpen(false); refreshReferral() }} />
      )}

      {/* Hero card */}
      <Card className='overflow-hidden border-0 shadow-md'>
        <div className='relative p-6' style={{ background: 'linear-gradient(135deg, #7583b2, #5a6aa0, #7583b2)' }}>
          <div className='flex items-start justify-between gap-3 mb-4'>
            <div className='flex items-start gap-3'>
              <div className='flex size-9 items-center justify-center rounded-full bg-white/20'><Gift size={18} className='text-white' /></div>
              <div>
                <h3 className='text-lg font-semibold text-white'>邀请有礼</h3>
                <p className='mt-1 text-xs text-white/75'>{canView ? '一级和二级可查看贡献流水、返佣金额、提现进度。' : '分享专属链接，邀请好友注册，双方均可获得赠送额度。'}</p>
              </div>
            </div>
            <Badge variant={canView ? 'default' : 'secondary'} className={canView ? 'bg-green-500' : ''}>{canView ? '返佣视图' : '邀请额度视图'}</Badge>
          </div>
          <div className='grid grid-cols-2 gap-3 lg:grid-cols-4'>
            {overviewStats.map(s => (
              <div key={s.label} className='min-w-0'><div className='truncate text-xs text-white/70'>{s.label}</div><div className='mt-1 text-lg font-semibold text-white sm:text-xl'>{s.value}</div></div>
            ))}
          </div>
        </div>
        <CardContent className='p-4'>
          <div className='flex flex-wrap gap-2 items-center justify-between'>
            <div className='flex gap-2'>
              {canView && <><Badge variant='outline' className='border-blue-200 bg-blue-50 text-blue-700'>{`一级返佣 ${fmtRatio(referralSummary?.referral_direct_ratio)}`}</Badge><Badge variant='outline' className='border-cyan-200 bg-cyan-50 text-cyan-700'>{`二级返佣 ${fmtRatio(referralSummary?.referral_indirect_ratio)}`}</Badge></>}
            </div>
            <div className='flex gap-2'>
              {canView && <Button size='sm' onClick={() => setWithdrawOpen(true)}><Wallet size={14} className='mr-1' />申请提现</Button>}
              <Button size='sm' variant='outline' disabled={!complianceConfirmed || !affQuota || affQuota <= 0} onClick={onOpenTransfer}>划转邀请额度</Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Stats grid */}
      <div className={`grid gap-4 ${canView ? 'xl:grid-cols-3' : 'xl:grid-cols-2'}`}>
        <Card><CardContent className='p-4 space-y-3'>
          <div className='flex items-center gap-2'><Copy size={16} className='text-muted-foreground' /><span className='font-medium'>邀请链接</span></div>
          <div className='flex gap-2'><Input value={affLink} readOnly className='flex-1' /><Button size='sm' onClick={copyLink}><Copy size={14} className='mr-1' />复制</Button></div>
          {!complianceConfirmed && (
            <div className='rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700'>
              非零值需先确认合规声明后才可划转或提现。
            </div>
          )}
        </CardContent></Card>
        <Card><CardContent className='p-4'>
          <div className='flex items-center gap-2 mb-3'><Gift size={16} className='text-muted-foreground' /><span className='font-medium'>邀请额度统计</span></div>
          <div className='grid grid-cols-2 gap-3'>
            {[{ label: '邀请额度余额', value: renderQuota(affQuota) },{ label: '赠送额度累计', value: renderQuota(referralSummary?.aff_history_quota || affQuota || 0) }].map(s => (
              <div key={s.label} className='rounded-lg border p-3'><div className='text-xs text-muted-foreground'>{s.label}</div><div className='mt-1 text-base font-semibold'>{s.value}</div></div>
            ))}
          </div>
        </CardContent></Card>
        {canView && (
          <Card><CardContent className='p-4'>
            <div className='flex items-center gap-2 mb-3'><CircleDollarSign size={16} className='text-muted-foreground' /><span className='font-medium'>返佣与下级统计</span></div>
            <div className='grid grid-cols-2 gap-2'>
              {commissionStats.map(s => (<div key={s.label} className='rounded-lg border p-2'><div className='text-xs text-muted-foreground'>{s.label}</div><div className='mt-1 text-sm font-semibold'>{s.value}</div></div>))}
            </div>
          </CardContent></Card>
        )}
      </div>

      {/* Tree view — only for admin / commission detail users */}
      {canView && (
      <Card><CardContent className='p-4'>
        <div className='mb-3 font-medium'>用户树</div>
        {referralTreeSummary ? (
          <>
            <div className='grid grid-cols-4 gap-3 mb-4'>
              {[{ label: '树总人数', val: referralTreeSummary?.total_nodes || 0 },{ label: '一级人数', val: referralTreeSummary?.level1_count || 0 },{ label: '二级人数', val: referralTreeSummary?.level2_count || 0 },{ label: '三级到N级', val: referralTreeSummary?.level3_and_beyond_count || 0 }].map(s => (
                <div key={s.label} className='rounded-lg border p-3 text-center'><div className='text-xs text-muted-foreground'>{s.label}</div><div className='mt-1 text-lg font-semibold'>{s.val}</div></div>
              ))}
            </div>
            <Table><TableHeader><TableRow><TableHead>ID</TableHead><TableHead>用户名</TableHead><TableHead>层级</TableHead><TableHead>直属下级数</TableHead><TableHead>贡献流水</TableHead><TableHead>返佣金额</TableHead></TableRow></TableHeader>
              <TableBody>{referralTreeRows.map(row => (<TableRow key={row.user_id}><TableCell>{row.user_id}</TableCell><TableCell>{row.username}</TableCell><TableCell>{row.depth}</TableCell><TableCell>{row.children_count}</TableCell><TableCell>{fmtMoney(row.contribution_amount)}</TableCell><TableCell>{fmtMoney(row.commission_amount)}</TableCell></TableRow>))}</TableBody>
            </Table>
          </>
        ) : <EmptyState msg='暂无用户树数据' hint='邀请好友注册后，这里将展示你的邀请关系树' />}
      </CardContent></Card>
      )}

      {/* Records tabs */}
      <Card><CardContent className='p-4'>
        <div className='mb-3 flex items-center justify-between'>
          <div className='font-medium'>邀请与返佣明细</div>
          {canView && (<div className='flex gap-1'>{[[0,'直推'],[1,'一级'],[2,'二级'],[3,'三级到N级']].map(([d,l]) => (<Button key={d} size='sm' variant={referralInviteDepth===d?'default':'outline'} onClick={()=>setReferralInviteDepth(Number(d))}>{l as string}</Button>))}</div>)}
        </div>
        <Tabs defaultValue='invites'>
          <TabsList><TabsTrigger value='invites'>邀请记录</TabsTrigger>{canView&&<><TabsTrigger value='commissions'>返佣记录</TabsTrigger><TabsTrigger value='withdrawals'>提现明细</TabsTrigger></>}</TabsList>
          <TabsContent value='invites'>
            {referralInvites.length>0 ? (
              <Table><TableHeader><TableRow><TableHead>ID</TableHead><TableHead>用户名</TableHead><TableHead>显示名称</TableHead><TableHead>注册时间</TableHead><TableHead>绑定关系</TableHead></TableRow></TableHeader>
                <TableBody>{referralInvites.map(r=>(<TableRow key={r.id}><TableCell>{r.id}</TableCell><TableCell>{r.username}</TableCell><TableCell>{r.display_name||'-'}</TableCell><TableCell>{ts2str(r.created_at)}</TableCell><TableCell className='flex gap-1'><Badge variant='secondary'>一级: {r.referral_level1_user_id||r.inviter_id||0}</Badge><Badge variant='secondary'>二级: {r.referral_level2_user_id||0}</Badge></TableCell></TableRow>))}</TableBody></Table>
            ) : <EmptyState msg='暂无邀请记录' hint='分享你的邀请链接，等待好友注册' />}
          </TabsContent>
          {canView&&<>
            <TabsContent value='commissions'>
              {referralCommissions.length>0 ? (
                <Table><TableHeader><TableRow><TableHead>ID</TableHead><TableHead>订单号</TableHead><TableHead>来源用户</TableHead><TableHead>层级</TableHead><TableHead>充值基数</TableHead><TableHead>返佣比例</TableHead><TableHead>返佣金额</TableHead><TableHead>到账时间</TableHead></TableRow></TableHeader>
                  <TableBody>{referralCommissions.map(r=>(<TableRow key={r.id}><TableCell>{r.id}</TableCell><TableCell className='max-w-[120px] truncate'>{r.trade_no}</TableCell><TableCell>{r.user_id}</TableCell><TableCell><Badge variant={r.level===1?'default':'secondary'}>{r.level===1?'一级':'二级'}</Badge></TableCell><TableCell>{fmtMoney(r.base_amount)}</TableCell><TableCell>{fmtRatio(r.ratio)}</TableCell><TableCell className='text-green-600 font-medium'>{fmtMoney(r.commission_amount)}</TableCell><TableCell>{ts2str(r.settled_at)}</TableCell></TableRow>))}</TableBody></Table>
              ) : <EmptyState msg='暂无返佣记录' hint='下级用户充值后，返佣记录将显示在这里' />}
            </TabsContent>
            <TabsContent value='withdrawals'>
              {referralWithdrawals.length>0 ? (
                <Table><TableHeader><TableRow><TableHead>ID</TableHead><TableHead>提现金额</TableHead><TableHead>到账金额</TableHead><TableHead>收款方式</TableHead><TableHead>收款人</TableHead><TableHead>状态</TableHead><TableHead>申请时间</TableHead></TableRow></TableHeader>
                  <TableBody>{referralWithdrawals.map(r=>{const sc:Record<string,string>={pending:'secondary',approved:'default',rejected:'destructive',paid:'default'};return (<TableRow key={r.id}><TableCell>{r.id}</TableCell><TableCell>{fmtMoney(r.amount)}</TableCell><TableCell>{fmtMoney(r.final_amount)}</TableCell><TableCell>{r.account_type||'-'}</TableCell><TableCell>{r.account_name||'-'}</TableCell><TableCell><Badge variant={(sc[r.status]||'secondary')as any}>{r.status||'-'}</Badge></TableCell><TableCell>{ts2str(r.created_at)}</TableCell></TableRow>)})}</TableBody></Table>
              ) : <EmptyState msg='暂无提现记录' hint='申请提现后，记录将显示在这里' />}
            </TabsContent>
          </>}
        </Tabs>
      </CardContent></Card>
    </div>
  )
}
