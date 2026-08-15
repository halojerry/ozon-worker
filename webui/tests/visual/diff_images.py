#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
webui/tests/visual/diff_images.py — 视觉基线像素 diff（Pillow）

用法：
  python3 diff_images.py --before <dir-before> --after <dir-after>
    --dir-before/before: 截图目录（capture_cdp.py --out 产物，按路由文件名对齐）
    --threshold 0.0      : 允许的像素差异比例（默认 0.0 = 无任何像素变化）
    --region             : 打印差异区域 bbox（便于定位）
退出码 0 = 无差异；1 = 存在差异（逐路由打印 diff ratio + bbox）。
"""

import argparse
import os
import sys

from PIL import Image, ImageChops


def diff_pair(before_path, after_path, threshold):
    a = Image.open(before_path).convert('RGB')
    b = Image.open(after_path).convert('RGB')
    if a.size != b.size:
        return {
            'ok': False,
            'reason': f'尺寸不一致 {a.size} vs {b.size}',
            'diff_pixels': -1,
            'ratio': 1.0,
            'bbox': None,
        }
    diff = ImageChops.difference(a, b)
    bbox = diff.getbbox()
    if bbox is None:
        return {'ok': True, 'reason': 'identical', 'diff_pixels': 0, 'ratio': 0.0, 'bbox': None}
    pixels = diff.crop(bbox)
    hist = pixels.histogram()
    diff_pixels = sum(hist[i] * i for i in range(256))  # 加权差异强度
    total = a.size[0] * a.size[1]
    ratio = diff_pixels / (total * 255 * 3)
    return {
        'ok': ratio <= threshold,
        'reason': f'diff_pixels={diff_pixels} ratio={ratio:.6f}',
        'diff_pixels': diff_pixels,
        'ratio': ratio,
        'bbox': bbox,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--before', required=True)
    ap.add_argument('--after', required=True)
    ap.add_argument('--threshold', type=float, default=0.0)
    args = ap.parse_args()

    before_files = sorted(f for f in os.listdir(args.before) if f.endswith('.png'))
    after_files = sorted(f for f in os.listdir(args.after) if f.endswith('.png'))
    common = [f for f in before_files if f in after_files]
    if not common:
        print('无共同截图文件可对比')
        sys.exit(1)
    missing = [f for f in after_files if f not in before_files]
    if missing:
        print(f'after 独有文件（跳过）: {missing}')

    failed = 0
    for name in common:
        result = diff_pair(
            os.path.join(args.before, name),
            os.path.join(args.after, name),
            args.threshold,
        )
        status = 'OK ' if result['ok'] else 'DIFF'
        if not result['ok']:
            failed += 1
        extra = f" bbox={result['bbox']}" if result['bbox'] else ''
        print(f'  [{status}] {name}: {result["reason"]}{extra}')

    print(f'\n{"✓ 全部页面无像素差异" if failed == 0 else f"✗ {failed} 个页面存在差异"}')
    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
