#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
webui/tests/visual/capture_cdp.py — Chrome CDP 冒烟截图（视觉基线工具）

背景：视觉回归基线（M2.4）首选 Playwright（tests/visual/smoke.spec.ts），
但 webui 仓库未安装 Playwright（依赖面刻意保持最小，仅 axios/react）。
本脚本提供等价的 **Chrome CDP 冒烟模式**（复用 skill 的 CDP 方案）：
headless Chrome → 注入 localStorage token（绕过登录）→ 逐路由截图 → baseline/。

用法：
  # 1. 构建并预览（生产产物，与线上一致）
  npm run build && npm run preview -- --port 4173 &   # http://localhost:4173/app/

  # 2. 截图（需要 skill/.venv314 —— 有 requests + websocket-client + Pillow）
  PYTHON=/Volumes/os/dev/ozon-worker/skill/.venv314/bin/python
  $PYTHON tests/visual/capture_cdp.py --out tests/visual/baseline/desktop \
      --base http://localhost:4173/app --routes login collect-box products stores tasks on-sale image-studio

  # 3. 像素 diff（改动前后对比，Pillow）
  $PYTHON tests/visual/diff_images.py --before <dir-before> --after <dir-after>

说明：
  - 注入 CSS 冻结全部动画（animation: none）→ 截图确定性（spinner 相位不漂移）；
    注入是测试态覆盖，不改应用源码。
  - 页面 /api 请求在静态 preview 下 404 → 渲染稳定错误/空态，前后可对比。
  - 截图尺寸：desktop 1440x900；--mobile 时额外抓 390x844（窄屏断点 768px 行为）。
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket  # websocket-client

ROUTES = {
    # 路由名 → (应用内路径, 是否需要登录 token)
    'login': ('/login', False),
    'collect-box': ('/collect-box', True),
    'products': ('/products', True),
    'stores': ('/stores', True),
    'tasks': ('/tasks', True),
    'on-sale': ('/on-sale', True),
    'image-studio': ('/image-studio', True),
}

FREEZE_ANIMATIONS_JS = """
(() => {
  // ⚠️ 顺序与时机敏感（实测两坑）：
  //  1) localStorage 注入必须放在 DOM 操作之前——new-document 脚本阶段
  //     document.documentElement 为 null，直接 appendChild 抛错会吞掉
  //     后面的 setItem（首轮截图全部登录页的根因）。
  //  2) 不得在解析期向 documentElement 挂样式——实测导致 body 解析中断
  //     （readyState=complete 但 document.body===null，全空白图）。
  //     必须等 DOMContentLoaded 后挂到 head。
  try {
    localStorage.setItem('ozon_webui_token', 'sk-qa-baseline-000000000000');
  } catch (e) { /* ignore */ }
  try {
    const style = document.createElement('style');
    style.setAttribute('data-qa-freeze', '1');
    style.textContent = '* { animation: none !important; transition: none !important; }';
    const mount = () => { (document.head || document.body).appendChild(style); };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
    else mount();
  } catch (e) { /* ignore */ }
})();
"""

SET_TOKEN_JS = ""


def find_chrome():
    candidates = [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        shutil.which('google-chrome'),
        shutil.which('chromium'),
        shutil.which('chrome'),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    raise SystemExit('Chrome 未找到：请安装 Chrome 或设置 CHROME_PATH')


def launch_chrome(chrome, profile_dir, port, window):
    cmd = [
        chrome,
        '--headless=new',
        f'--remote-debugging-port={port}',
        '--remote-allow-origins=*',
        f'--user-data-dir={profile_dir}',
        f'--window-size={window[0]},{window[1]}',
        '--disable-gpu',
        '--hide-scrollbars',
        '--no-first-run',
        '--no-default-browser-check',
        '--force-prefers-reduced-motion',
        'about:blank',
    ]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def get_page_ws(port):
    # 复用首个 tab（about:blank）；新 Chrome 一定有
    for _ in range(50):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list') as r:
                pages = json.loads(r.read())
            ws = next(p['webSocketDebuggerUrl'] for p in pages if p.get('type') == 'page')
            return ws
        except Exception:
            time.sleep(0.2)
    raise SystemExit('CDP 调试端口未就绪')


def cdp_send(ws, method, params=None, msg_id=1):
    ws.send(json.dumps({'id': msg_id, 'method': method, 'params': params or {}}))
    while True:
        raw = ws.recv()
        msg = json.loads(raw)
        if msg.get('id') == msg_id:
            return msg
        # 丢弃事件帧


def wait_document(ws, timeout=15.0):
    """轮询文档就绪：readyState==='complete' 且 body 已挂载。
    固定 sleep 在 Chrome 启动竞态下会截到 body 未解析的帧（空白图），
    轮询 + 超时兜底（超时则重导航一次，见 capture()）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = cdp_send(ws, 'Runtime.evaluate', {
            'expression': "document.readyState + '|' + (document.body ? '1' : '0')",
            'returnByValue': True,
        }, msg_id=20)
        value = r.get('result', {}).get('result', {}).get('value', '')
        if value == 'complete|1':
            return True
        time.sleep(0.2)
    return False


def capture(ws, url, out_path):
    cdp_send(ws, 'Page.navigate', {'url': url}, msg_id=10)
    if not wait_document(ws):
        # 启动竞态兜底：重导航一次
        print(f'  … {url} 首次就绪超时，重导航')
        cdp_send(ws, 'Page.navigate', {'url': url}, msg_id=12)
        wait_document(ws)
    time.sleep(1.2)  # fetch 404 → 错误态渲染稳定窗口
    resp = cdp_send(ws, 'Page.captureScreenshot', {'format': 'png'}, msg_id=11)
    data = resp['result']['data']
    with open(out_path, 'wb') as f:
        f.write(base64.b64decode(data))
    print(f'  ✓ {out_path} ({os.path.getsize(out_path) // 1024} KB)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True, help='截图输出目录（如 tests/visual/baseline/desktop）')
    ap.add_argument('--base', default='http://localhost:4173/app', help='应用 base URL')
    ap.add_argument('--routes', nargs='*', default=list(ROUTES), help='路由名列表')
    ap.add_argument('--port', type=int, default=9223, help='CDP 调试端口')
    ap.add_argument('--window', default='1440x900', help='桌面视口')
    ap.add_argument('--mobile', action='store_true', help='额外抓 390x844 移动端')
    args = ap.parse_args()

    chrome = os.environ.get('CHROME_PATH') or find_chrome()
    profile = tempfile.mkdtemp(prefix='omo-webui-qa-')
    w, h = (int(v) for v in args.window.split('x'))

    proc = launch_chrome(chrome, profile, args.port, (w, h))
    try:
        ws_url = get_page_ws(args.port)
        ws = websocket.create_connection(ws_url, timeout=20)

        # 注入：冻结动画 + 预设登录 token（保护路由可渲染）
        cdp_send(ws, 'Page.enable', {}, msg_id=1)
        cdp_send(ws, 'Runtime.enable', {}, msg_id=2)
        cdp_send(ws, 'Page.addScriptToEvaluateOnNewDocument', {
            'source': FREEZE_ANIMATIONS_JS + SET_TOKEN_JS,
        }, msg_id=3)
        cdp_send(ws, 'Emulation.setDeviceMetricsOverride', {
            'width': w, 'height': h, 'deviceScaleFactor': 1, 'mobile': False,
        }, msg_id=4)

        os.makedirs(args.out, exist_ok=True)
        print(f'capture → {args.out} (viewport {w}x{h})')
        for name in args.routes:
            if name not in ROUTES:
                print(f'  ✗ 未知路由 {name}')
                continue
            path, need_auth = ROUTES[name]
            capture(ws, f'{args.base}{path}', os.path.join(args.out, f'{name}.png'))

        if args.mobile:
            mdir = args.out.replace('desktop', 'mobile')
            os.makedirs(mdir, exist_ok=True)
            cdp_send(ws, 'Emulation.setDeviceMetricsOverride', {
                'width': 390, 'height': 844, 'deviceScaleFactor': 1, 'mobile': True,
            }, msg_id=5)
            print(f'capture → {mdir} (viewport 390x844)')
            for name in args.routes:
                if name not in ROUTES:
                    continue
                path, _ = ROUTES[name]
                capture(ws, f'{args.base}{path}', os.path.join(mdir, f'{name}.png'))
        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)

    print('done.')


if __name__ == '__main__':
    main()
