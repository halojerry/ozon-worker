/*
Copyright (C) 2023-2026 QuantumNous — GNU AGPL v3
*/
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

interface AsstCard {
  name: string; title: string; bio: string; bg: string; letter: string; online: boolean
  img?: string; work?: string
}

const IMG = (n: string) => `/landing/${n}.jpg`

const ROW1: AsstCard[] = [
  { name: 'Patrick · PPT 制作师', title: '演示文稿专家', bio: '"给我要点，还你精致 PPT。"', bg: '#ff6b35', letter: 'P', online: true, img: IMG('ppt-creator'), work: '正在做 PPT...' },
  { name: 'Emily · Excel 数据师', title: '数据分析专家', bio: '"透视、图表、数据清洗。"', bg: '#00b42a', letter: 'E', online: true, img: IMG('excel-creator'), work: '正在分析数据...' },
  { name: 'Warren · 财务建模师', title: '金融建模专家', bio: '"DCF、股权表、三大报表。"', bg: '#165dff', letter: 'W', online: true, img: IMG('financial-model-creator'), work: '正在建模...' },
  { name: 'Albert · 论文写作师', title: '学术写作专家', bio: '"提纲或全文，一手交稿。"', bg: '#722ed1', letter: 'A', online: true, img: IMG('academic-paper'), work: '正在写论文...' },
  { name: 'Stella · UI/UX 设计师', title: '界面设计专家', bio: '"按最佳实践做设计。"', bg: '#ec4899', letter: 'S', online: true, img: IMG('ui-ux-pro-max'), work: '正在做设计...' },
  { name: 'Marco · 动态 PPT 师', title: '3D 演示专家', bio: '"电影感的演示。"', bg: '#f59e0b', letter: 'M', online: true, img: IMG('morph-ppt'), work: '正在做动画...' },
  { name: 'William · Word 文档师', title: '文档撰写专家', bio: '"报告、方案、信函。"', bg: '#0e73cc', letter: 'W', online: true, img: IMG('word-creator'), work: '正在写文档...' },
  { name: 'Carlos · 协作 Agent', title: '全能协作助手', bio: '"复杂任务，端到端完成。"', bg: '#4b3c7a', letter: 'C', online: true, img: IMG('cowork'), work: '正在协作...' },
]

const ROW2: AsstCard[] = [
  { name: 'David · Pitch Deck', title: '路演 PPT 专家', bio: '"投资人更新，一击即中。"', bg: '#d42ca0', letter: 'D', online: true, img: IMG('pitch-deck-creator'), work: '正在准备路演...' },
  { name: 'Raj · 仪表盘大师', title: '数据可视化专家', bio: '"把数据变成洞察。"', bg: '#6366f1', letter: 'R', online: true, img: IMG('dashboard-creator'), work: '正在做图表...' },
  { name: 'Oliver · 图表专家', title: '流程图 & 脑图', bio: '"复杂关系一目了然。"', bg: '#0891b2', letter: 'O', online: true, img: IMG('beautiful-mermaid'), work: '正在画图...' },
  { name: 'Sam · 游戏开发', title: '3D 游戏制作', bio: '"想法变成可玩原型。"', bg: '#7c3aed', letter: 'S', online: true, img: IMG('game-3d'), work: '正在做游戏...' },
  { name: 'Chloe · 角色扮演', title: '故事互动大师', bio: '"沉浸式故事体验。"', bg: '#eab308', letter: 'C', online: true, img: IMG('story-roleplay'), work: '正在编故事...' },
  { name: 'James · 教练', title: '个人成长教练', bio: '"目标、复盘、带你达成。"', bg: '#ef4444', letter: 'J', online: false, img: IMG('human-3-coach'), work: undefined },
  { name: 'Maya · 3D PPT', title: '三维演示专家', bio: '"让 PPT 跳出屏幕。"', bg: '#14b8a6', letter: 'M', online: true, img: IMG('morph-ppt-3d'), work: '正在做 3D...' },
  { name: 'Sophia · 社媒运营', title: '社交媒体专家', bio: '"自动发布、数据追踪。"', bg: '#1d9bf0', letter: 'S', online: false, img: IMG('social-job-publisher'), work: undefined },
]

function MarqueeRow({ cards, direction }: { cards: AsstCard[]; direction: 'left' | 'right' }) {
  const { t } = useTranslation()
  const [paused, setPaused] = useState(false)
  const duplicated = [...cards, ...cards, ...cards]

  return (
    <div
      className={`asst-marquee-row ${direction === 'left' ? 'row1' : 'row2'}`}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      style={{ animationPlayState: paused ? 'paused' : 'running' }}
    >
      {duplicated.map((card, i) => (
        <div
          key={`${card.name}-${i}`}
          className='asst-card'
          data-work={card.work}
          onMouseEnter={e => {
            if (!card.work) return
            const bubble = document.getElementById('asst-bubble')
            if (!bubble) return
            const rect = e.currentTarget.getBoundingClientRect()
            const wrap = e.currentTarget.closest('.asst-carousel-wrap')
            const wrapRect = wrap?.getBoundingClientRect()
            if (wrapRect) {
              bubble.style.left = `${rect.left - wrapRect.left + rect.width / 2}px`
              bubble.style.top = `${rect.top - wrapRect.top - 12}px`
              bubble.textContent = card.work
              bubble.classList.add('show')
              bubble.classList.remove('hide')
            }
          }}
          onMouseLeave={() => {
            const bubble = document.getElementById('asst-bubble')
            if (bubble) { bubble.classList.add('hide'); bubble.classList.remove('show') }
          }}
        >
          <div className='asst-card-avatar-area'>
            <img src={card.img} alt={card.name} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
            <span className={`asst-card-status ${card.online ? 'online' : 'busy'}`} />
          </div>
          <div className='asst-card-info'>
            <div className='asst-card-name'>{card.name}</div>
            <div className='asst-card-title'>{card.title}</div>
            <div className='asst-card-bio'>{t(card.bio)}</div>
          </div>
        </div>
      ))}
    </div>
  )
}

export function Assistants() {
  const { t } = useTranslation()

  return (
    <section className='assistants-section reveal' id='assistants'>
      <div className='assistants-inner'>
        <p className='section-label'>{t('内置助手')}</p>
        <h2 className='section-title'>{t('认识你的团队。')}</h2>
        <p className='section-sub'>
          {t('20+ 位助手住在你的电脑里，从 PPT 制作到论文写作，从 Excel 数据到 UI 设计——每个都是某个领域的专家。')}
        </p>

        <div className='asst-carousel-wrap-outer'>
          <div className='asst-carousel-wrap' id='asstWrap'>
            {/* Floating work bubble */}
            <div id='asst-bubble' className='asst-work-bubble' />
            <MarqueeRow cards={ROW1} direction='left' />
            <MarqueeRow cards={ROW2} direction='right' />
          </div>
        </div>

        <p className='asst-more'>{t('…and every OpenAI-compatible endpoint. Just add the base URL.')}</p>
      </div>
    </section>
  )
}
