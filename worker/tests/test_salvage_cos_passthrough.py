"""fix/image-ref-cos-whitelist-v1 批3（计划 T4）：E1 salvage 对已托管 COS 图直通。

背景：镜像草稿场景（draft_image_mirror 把 draft.images 回写为本方 COS URL）下，
E1 `salvage_original_images` 会把**已是本方 COS 的 URL** 再下载-再上传一轮
（同桶冗余转存）。改为直通：输入已是本方 COS → 原样收下（零网络、计入
saved/max_n）。cos_enabled()=False 的整体早退语义保持不变。

运行（无需 PG/GPU/网络）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_salvage_cos_passthrough.py -q
"""
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

# 环境隔离：COS_* 可能来自生产 .env（storage/db.py 模块级 load_dotenv 注入），
# import 期先清一次，后续由 autouse fixture 逐用例兜底。
for _k in list(os.environ.keys()):
    if _k.startswith("COS_"):
        os.environ.pop(_k, None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.cos_uploader import _stable_key, salvage_original_images  # noqa: E402

# 本方 COS 托管图（镜像回写形态，同 test_image_url_guard.py 的事故 URL）
COS_MIRROR = ("https://yss-1256275613.cos.ap-guangzhou.myqcloud.com"
              "/draft-images/8dbf02b9f.jpg")
COS_MIRROR_2 = ("https://yss-1256275613.cos.ap-guangzhou.myqcloud.com"
                "/draft-images/aaaabbbb.jpg")
COS_MIRROR_3 = ("https://yss-1256275613.cos.ap-guangzhou.myqcloud.com"
                "/draft-images/ccccdddd.jpg")
GOOD_ORIGINAL = "https://cbu01.alicdn.com/img/ibank/O1CNgood_!!123456789-0-cib.jpg"
COS_THUMBNAIL = ("https://yss-1256275613.cos.ap-guangzhou.myqcloud.com"
                 "/draft-images/x_310x310.jpg")

_COS_ENV = {
    "COS_SECRET_ID": "test-sid",
    "COS_SECRET_KEY": "test-skey",
    "COS_BUCKET": "test-bucket",
}


@pytest.fixture(autouse=True)
def _cos_env_sandbox():
    """每用例前清空 COS_*（不依赖用例自身），结束后恢复现场。"""
    backup = {k: v for k, v in os.environ.items() if k.startswith("COS_")}
    for k in backup:
        os.environ.pop(k, None)
    yield
    for k in list(os.environ.keys()):
        if k.startswith("COS_"):
            os.environ.pop(k, None)
    os.environ.update(backup)


@pytest.fixture()
def _with_cos():
    """注入测试 COS 凭证（cos_enabled=True；上传本身被 mock，绝不真连）。"""
    os.environ.update(_COS_ENV)


def test_a_mixed_list_passthrough_order_preserved(_with_cos):
    """a. [COS 镜像, alicdn 原图] → COS 直通（零下载）、alicdn 正常转存、顺序保持。"""
    uploaded = (f"https://test-bucket.cos.ap-guangzhou.myqcloud.com/"
                f"{_stable_key(GOOD_ORIGINAL, 'ozon-1688')}")
    with mock.patch("requests.get") as m_get, \
            mock.patch("utils.cos_uploader.cos_upload_bytes") as m_up:
        m_up.side_effect = (
            lambda data, key, content_type="image/jpeg":
                f"https://test-bucket.cos.ap-guangzhou.myqcloud.com/{key}")
        m_get.return_value = mock.Mock(status_code=200, content=b"img-bytes")
        out = salvage_original_images([COS_MIRROR, GOOD_ORIGINAL])
    # COS URL 原样直通（不经历 下载→转存 的 URL 换皮）
    assert out == [COS_MIRROR, uploaded], out
    # alicdn 原图恰好发起 1 次下载（COS URL 零网络）
    assert m_get.call_count == 1, m_get.call_args_list
    assert m_get.call_args.args[0] == GOOD_ORIGINAL
    # 转存恰好 1 次（只有 alicdn 那张）
    assert m_up.call_count == 1


def test_b_all_cos_zero_network(_with_cos):
    """b. 输入全 COS → 零网络调用（不下载不上传），全部原样直通。"""
    with mock.patch("requests.get") as m_get, \
            mock.patch("utils.cos_uploader.cos_upload_bytes") as m_up:
        out = salvage_original_images([COS_MIRROR, COS_MIRROR_2])
    assert out == [COS_MIRROR, COS_MIRROR_2], out
    m_get.assert_not_called()
    m_up.assert_not_called()


def test_c_cos_disabled_returns_empty():
    """c. cos_enabled=False（env 未配）→ 返回 []（既有整体语义回归，直通不放行）。"""
    assert salvage_original_images([COS_MIRROR, GOOD_ORIGINAL]) == []


def test_d_passthrough_respects_max_n(_with_cos):
    """d. 直通结果计入 saved/max_n——max_n=2 时第 3 张 COS 被截断，零网络。"""
    with mock.patch("requests.get") as m_get, \
            mock.patch("utils.cos_uploader.cos_upload_bytes") as m_up:
        out = salvage_original_images(
            [COS_MIRROR, COS_MIRROR_2, COS_MIRROR_3], max_n=2)
    assert out == [COS_MIRROR, COS_MIRROR_2], out
    m_get.assert_not_called()
    m_up.assert_not_called()


def test_e_cos_thumbnail_still_skipped(_with_cos):
    """护栏：直通置于 _is_reference_image 之后——COS 域缩略后缀（_310x310）
    照样跳过（批1「缩略恒拒对 COS 域生效」不变式不因直通破口）。"""
    with mock.patch("requests.get") as m_get, \
            mock.patch("utils.cos_uploader.cos_upload_bytes") as m_up:
        out = salvage_original_images([COS_THUMBNAIL, COS_MIRROR])
    assert out == [COS_MIRROR], out
    m_get.assert_not_called()
    m_up.assert_not_called()
