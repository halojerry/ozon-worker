#!/usr/bin/env python3
"""跨平台货源 v1 批4 — reference_images 平台感知 TDD（纯函数，零 I/O）。

前置硬警报（PLAN §4 批4 item 0，批2/批3 review 实查成立）：bad-token 表含
``img.alicdn.com/imgextra``——这既是 1688 页面 UI 噪音（评价头像/跨产品污染）
也是**淘宝/天猫主图唯一 CDN 形态**；1688 域白名单又不含 pddpic——pdd 主图全拒。
不修则淘宝信封图空 → validate「产品图片为空」硬阻断。

覆盖（两侧都锁）：
- taobao/tmall：imgextra 形态主图放行（get_best_product_images 有图）；
- 1688：imgextra 仍拒（原滤网逐字节保留，default 参数=旧行为）；
- pdd：pddpic/pinduoduo/yangkeduo 域放行（1688 白名单外照拒）；
- 平台感知只豁免 imgextra 两令牌——其余噪音词（logo/icon/小尺寸）新平台照拒。

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_reference_images_platform_v1.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib.reference_images import (  # noqa: E402
    get_best_product_images,
    is_likely_product_image,
)

# 淘宝主图真实形态（批2 mtop 夹具同源：img.alicdn.com/imgextra/...）
_TB_MAIN = "https://img.alicdn.com/imgextra/i4/O1CN01main/!!6000000000000-0-tp.jpg"
_TB_SECOND = "https://img.alicdn.com/imgextra/i2/O1CN01second/!!6000000000001-0-tp.jpg"
_TM_MAIN = "https://img.alicdn.com/imgextra/i1/O1CN01tmall/!!6100000000000-0-tp.jpg"
# pdd 主图真实形态（批3 适配器归一：https://img.pddpic.com/...）
_PDD_MAIN = "https://img.pddpic.com/mms-material-msg/2023-01-01/test.jpeg"
# 1688 主图真实形态（cbu01，不受本批影响）
_1688_MAIN = "https://cbu01.alicdn.com/img/ibank/2020/testmain.jpg"


class TestTaobaoTmallImgextraAccepted:
    def test_taobao_main_image_imgextra_accepted(self):
        assert is_likely_product_image(_TB_MAIN, platform="taobao") is True

    def test_tmall_main_image_imgextra_accepted(self):
        assert is_likely_product_image(_TM_MAIN, platform="tmall") is True

    def test_get_best_product_images_keeps_taobao_mains(self):
        out = get_best_product_images([_TB_MAIN, _TB_SECOND], platform="taobao")
        # 两图都活（imgextra 豁免生效）；顺序按 reference_priority 既有规则
        # （较长 URL 排前），非本批语义
        assert out == [_TB_SECOND, _TB_MAIN]

    def test_1688_main_image_unaffected(self):
        assert is_likely_product_image(_1688_MAIN, platform="taobao") is True
        assert is_likely_product_image(_1688_MAIN) is True


class Test1688BadTokenLocked:
    def test_1688_imgextra_still_rejected(self):
        """test-locked：imgextra 滤网对 1688 原样保留（评价头像/跨产品污染）。"""
        assert is_likely_product_image(_TB_MAIN, platform="1688") is False
        assert is_likely_product_image(_TB_MAIN) is False  # default=1688 逐字节旧行为

    def test_get_best_product_images_1688_default_still_empty(self):
        assert get_best_product_images([_TB_MAIN, _TB_SECOND]) == []

    def test_gw_imgextra_1688_still_rejected(self):
        url = "https://img.alicdn.com/imgextra/gw-avatars/x.jpg"
        assert is_likely_product_image(url, platform="1688") is False


class TestPddHostAccepted:
    def test_pdd_main_image_accepted(self):
        assert is_likely_product_image(_PDD_MAIN, platform="pdd") is True

    def test_pdd_host_rejected_on_1688_default(self):
        """1688 白名单外照拒（不扩 1688 语义）。"""
        assert is_likely_product_image(_PDD_MAIN) is False

    def test_get_best_product_images_pdd(self):
        out = get_best_product_images([_PDD_MAIN], platform="pdd")
        assert out == [_PDD_MAIN]


class TestNoiseStillFilteredOnNewPlatforms:
    def test_logo_token_still_rejected_on_taobao(self):
        """平台豁免仅限 imgextra 两令牌——其余噪音词照拒。"""
        url = "https://img.alicdn.com/imgextra/i4/store_logo.jpg"
        assert is_likely_product_image(url, platform="taobao") is False

    def test_small_size_still_rejected_on_taobao(self):
        url = "https://img.alicdn.com/imgextra/i4/preview_60x60.jpg"
        assert is_likely_product_image(url, platform="taobao") is False

    def test_avatar_token_still_rejected_on_pdd(self):
        url = "https://img.pddpic.com/avatar/user.jpg"
        assert is_likely_product_image(url, platform="pdd") is False
