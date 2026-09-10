"""跨平台货源扩展 v1 批1:source_candidates offer_id 多平台解析单测。

1688 之外的货源 URL(淘宝 item.htm?id= / 拼多多 goods.html?goods_id=)此前
提取不到 offer_id,唯一键退化成整条 URL;批1 补两族模式,未知形态仍回退
整条 URL(现状不变)。纯函数测试,无需 PG。

运行(无需 PG/GPU):
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_offer_id_multi_platform.py -q
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services.source_candidate_service import _offer_id_from_url


def test_1688_offer_url_unchanged():
    """1688 offer URL → 数字 offer_id(现状行为锁死)。"""
    assert _offer_id_from_url("https://detail.1688.com/offer/123456789.html") == "123456789"
    assert _offer_id_from_url(
        "https://detail.1688.com/offer/123456789.html?tracelog=x") == "123456789"


def test_taobao_tmall_id_param():
    """淘宝/天猫 item.htm?id= → 数字 id(锚定 ?/& 防误吃子串)。"""
    assert _offer_id_from_url("https://item.taobao.com/item.htm?id=679394077935") == "679394077935"
    assert _offer_id_from_url(
        "https://item.taobao.com/item.htm?spm=a1z10.5&id=679394077935&skuId=1") == "679394077935"
    assert _offer_id_from_url("https://detail.tmall.com/item.htm?id=679394077935") == "679394077935"
    assert _offer_id_from_url(
        "https://chaoshi.detail.tmall.com/item.htm?id=111222333") == "111222333"


def test_pdd_goods_id_param():
    """拼多多 goods.html?goods_id= → 数字 goods_id(goods1/goods2 变体同族)。"""
    assert _offer_id_from_url(
        "https://mobile.yangkeduo.com/goods.html?goods_id=8888888888") == "8888888888"
    assert _offer_id_from_url(
        "https://mobile.yangkeduo.com/goods1.html?_wvx=10&goods_id=8888888888") == "8888888888"
    assert _offer_id_from_url(
        "https://mobile.pinduoduo.com/goods.html?goods_id=8888888888") == "8888888888"


def test_no_false_positive_on_lookalike_params():
    """误吃防护:aid=/skuId=/goodsId= 等形近参数不提取。"""
    assert _offer_id_from_url("https://example.com/item.htm?aid=999") == ""
    assert _offer_id_from_url(
        "https://item.taobao.com/item.htm?skuId=999&id=1") == "1"  # id= 仍命中
    assert _offer_id_from_url("https://mobile.yangkeduo.com/goods.html?x_goods_id=7") == ""


def test_unknown_form_falls_back_empty():
    """未知形态(短链/异域)→ ""(调用方回退整条 URL 作唯一键,现状不变)。"""
    assert _offer_id_from_url("https://p.pinduoduo.com/AbCdEf") == ""
    assert _offer_id_from_url("https://example.com/some/page") == ""
    assert _offer_id_from_url("") == ""
    assert _offer_id_from_url(None) == ""
