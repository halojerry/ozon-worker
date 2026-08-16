/*
Copyright (C) 2023-2026 QuantumNous — GNU AGPL v3
*/
import { useTranslation } from 'react-i18next'

export function Faq() {
  const { t } = useTranslation()

  const faqs = [
    {
      q: 'POUNDING 到底能帮我做什么？',
      a: '把它想成一块共享桌面——Claude Code、Codex、Gemini CLI 和你的助手们在上面一起干活。你发指令，它们自己分工、互相接手，事情 24 小时往前推。',
    },
    {
      q: '我不在电脑前，它还能工作吗？',
      a: '能。用 Telegram、微信、飞书或钉钉远程发指令，Agent 在你电脑上 24/7 运行。定时任务也会准时触发——你睡觉，活儿照样干。',
    },
    {
      q: '用 POUNDING 安全吗？',
      a: 'POUNDING 直连各大模型 API，你的请求不经过任何中转服务器。所有 API Key 加密存储在本地。你的数据、你的电脑、你的规则。',
    },
    {
      q: '不会写代码能用吗？',
      a: '能。内置了 20+ 位助手——做 PPT、算数据、写论文、P 图——点一下就能用。不需要写一行代码。',
    },
    {
      q: '和 Cursor / Copilot 有什么不同？',
      a: 'Cursor/Copilot 是代码编辑器里的 AI 辅助。POUNDING 是一个桌面——管理你所有的 CLI Agent，还有 20+ 位非编程助手。做 PPT、算 Excel、改图——这些 Cursor 做不了。',
    },
    {
      q: '我已经有 Claude Code，装 POUNDING 会冲突吗？',
      a: '不会。POUNDING 自动发现你已有的 CLI Agent，加上自己的 20+ 位助手，统一管理。零迁移成本。',
    },
    {
      q: 'POUNDING 是免费的吗？',
      a: '是的！POUNDING 完全开源（Apache 2.0），免费使用。你只需要付模型 API 的费用（很多厂商有免费额度）。',
    },
    {
      q: '有 bug 怎么反馈？',
      a: '去 GitHub Issues 提 issue，或者在 Discord 社区里直接聊。开源社区很活跃，通常几小时内就有人回复。',
    },
  ]

  return (
    <section className='faq-section reveal' id='faq'>
      <div className='faq-inner'>
        <h2 className='section-title' style={{ textAlign: 'center' }}>常见<br />问题</h2>
        <p className='section-sub' style={{ textAlign: 'center', maxWidth: '640px', margin: '0 auto 56px' }}>
          大家在装之前最常问的几个问题。
        </p>

        <div className='faq-list'>
          {faqs.map((faq, i) => (
            <div key={i} className='faq-item'>
              <details>
                <summary>
                  <span className='faq-q'>{faq.q}</span>
                  <span className='faq-chev'>
                    <svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2.5' strokeLinecap='round'><path d='m6 9 6 6 6-6'/></svg>
                  </span>
                </summary>
                <p className='faq-a'>{faq.a}</p>
              </details>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
