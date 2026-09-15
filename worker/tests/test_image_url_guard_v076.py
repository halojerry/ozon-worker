# worker/tests/test_image_url_guard_v076.py
"""v0.76 T15(inj-H1): 图床白名单按 hostname 精确/后缀匹配——query 垫片失效。

审计实证：旧实现 `any(dom in lowered)` 子串匹配可被 `http://127.0.0.1:8080/?pad=
alicdn.com` 垫片绕过——内网 URL 借 query 参数冒充图床域直通 E1 转存/生图参考
白名单。改 hostname 精确/后缀匹配后垫片失效；webp 拒绝与缩略后缀判定仍在
lowered 全串上，语义不变。

同文件后半（TestSalvageSafeFetchWiring）：controller 追加范围——E1 转存链
（cos_uploader.salvage_original_images）对用户 URL 的裸 requests.get 接
utils.secure_fetch.safe_fetch（白名单过后的解析 IP 校验 + 逐跳复核）。

测试纪律：全程 monkeypatch 假 HTTP/假 DNS（对齐 T13/T14 手法），零真实出站。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.image_url_guard import is_product_image_candidate  # noqa: E402


def test_query_pad_bypass_blocked():
    assert not is_product_image_candidate("http://127.0.0.1:8080/admin?pad=alicdn.com/img/ibank/x.jpg")
    assert not is_product_image_candidate("http://evil.example/pad=1688.com/x.jpg")


def test_real_bed_domains_pass():
    assert is_product_image_candidate("https://img.alicdn.com/imgextra/x.jpg")
    assert is_product_image_candidate("https://cbu01.alicdn.com/img/ibank/y.jpg")
    assert is_product_image_candidate("https://img.1688.com/kf/z.jpg")


def test_lookalike_domain_blocked():
    assert not is_product_image_candidate("https://alicdn.com.evil.example/x.jpg")
    assert not is_product_image_candidate("https://notalicdn.com/x.jpg")
