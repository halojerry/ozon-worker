/**
 * Tailwind CSS v4 兼容配置。
 *
 * ⚠️ v4 的主题（颜色/字体/圆角/阴影/动效）以 CSS-first 方式定义在
 * `src/styles/tokens.css` 的 `@theme` 块中 —— 本文件仅供旧工具链
 * （IDE 插件 / 静态扫描）读取内容路径，主题不在此重复定义（防双源漂移）。
 */
import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  // theme 见 src/styles/tokens.css @theme（唯一事实源 = design-deliverables/design-tokens.json）
} satisfies Config
