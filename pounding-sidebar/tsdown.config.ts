/**
 * pounding-sidebar 独立 tsdown 配置（参照社区标准消费者插件 dsh-sentinel 的构建模板）：
 *
 * - node half：src/index.ts → lib/index.js（ESM，@deepseek-ai/cordis 保持 external，
 *   由宿主 Loader 解析，零 npm 安装）
 * - client half：src/client/index.tsx → lib/client.js（CJS 闭包 bundle，
 *   externals 恰为平台 seed 模块，其余全部 inline；以 window.__ModuleLoader__ 注册，
 *   bundle id = 包名 pounding-sidebar，与官方 profile 通道约定一致）
 *
 * 纯类型交互：跨插件协作只走 ctx.betterSidebar 服务方法，value-import
 * dsh-better-sidebar 会被 client 构建纯度门拒绝（类型 import 编译期擦除，无碍）。
 * 本插件样式使用内联 style，无需 CSS module 管线。
 */
import type { UserConfig } from 'tsdown'

const PLUGIN_ID = 'pounding-sidebar'
const PLATFORM_MODULES = [
  'react',
  'react/jsx-runtime',
  'react-dom',
  'react-dom/client',
  'cordis',
  '@deepseek-ai/dsh-client-ui-slots',
  '@deepseek-ai/dsh-client-web-react',
  '@deepseek-ai/dsh-client-ui-primitives',
  '@deepseek-ai/dsh-client-schema-form',
] as const
const RUNTIME_STORE_EXEMPTION = '@deepseek-ai/dsh-client-runtime/client'
const EXTERNALS: readonly string[] = [...PLATFORM_MODULES, RUNTIME_STORE_EXEMPTION]

export default [
  {
    name: `${PLUGIN_ID}/node`,
    entry: ['src/index.ts'],
    outDir: 'lib',
    format: ['esm'],
    platform: 'node',
    target: 'es2024',
    fixedExtension: false,
    dts: false,
    clean: false,
    external: [/^@deepseek-ai\//, 'cordis'],
  },
  {
    name: `${PLUGIN_ID}/client`,
    entry: { client: 'src/client/index.tsx' },
    outDir: 'lib',
    format: 'cjs',
    platform: 'browser',
    dts: false,
    sourcemap: true,
    clean: false,
    external: [...EXTERNALS],
    define: {
      'process.env.NODE_ENV': JSON.stringify(process.env.NODE_ENV ?? 'production'),
      'import.meta.env.MODE': JSON.stringify(process.env.NODE_ENV ?? 'production'),
      'import.meta.env': JSON.stringify({ MODE: process.env.NODE_ENV ?? 'production' }),
    },
    noExternal: (id: string) => (EXTERNALS.includes(id) ? undefined : true),
    outputOptions: {
      entryFileNames: 'client.js',
      banner: `window.__ModuleLoader__.load({ id: ${JSON.stringify(PLUGIN_ID)}, factory: (require) => {`,
      footer: 'return module.exports; } });',
      intro: 'var module = { exports: {} }; var exports = module.exports;',
      codeSplitting: false,
    },
  },
] satisfies UserConfig[]
