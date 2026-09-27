"""fix/handover-batch-v1: is_cos_url 域判定 hostname 感知收紧（09-findings #4-图片）。

旧实现对**整条 URL 裸子串**判定（`.myqcloud.com` in url or `cos.` in url）：
`mycos.evil.com/file/images/x.jpg`、查询串垫片（`?pad=cos.x`）都判本方 COS——
「唯一事实源」第一层防线名不副实，上卡闸只靠 key 前缀兜住。现改 urlparse 取
host 按域判定：myqcloud 后缀 / COS_PUBLIC_DOMAIN 自定义公网域 / `cos` 须为
完整域标签；路径与查询串不参与判定。

锁定面（is_cos_url 唯一实现 utils/image_url_guard，消费方经 cos_uploader
re-export 零改动）：
  1. 伪装域拒：mycos.evil.com / 路径·查询串垫片
  2. 合法形态过：区域域 / 全球加速 / 非 myqcloud 的 cos 标签域 / 自定义公网域
  3. 分类闸联动：classify_image_source 对伪装域 file/images/ 判 external
     （收紧前判 ai——即本批真正堵住的口子）
  4. 既有契约不回归：非 str/空串 → True；myqcloud 全形态照旧

运行（无需 PG/GPU）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_url_guard_hostname_v080.py -q
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.image_source import classify_image_source, enforce_upload_policy
from utils.image_url_guard import (
    filter_product_images,
    is_cos_url,
    is_product_image_candidate,
)

# ⚠️ 与 test_ref_image_pollution_guard 同款隔离：节点/服务 import 链会触发
# storage/db.py 的 load_dotenv()（注入本地 deploy/.env 的 COS_*，含可选
# COS_PUBLIC_DOMAIN），污染本文件「env 缺省」前提——import 后统一清空。
for _k in list(os.environ.keys()):
    if _k.startswith("COS_"):
        os.environ.pop(_k, None)


# 合法形态（既有测试已覆盖，此处留代表防回归）
COS_REGION = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/a.jpg"
COS_ACCELERATE = "https://yss-1256275613.cos.accelerate.myqcloud.com/draft-images/x.jpg"
COS_BEIJING = "https://bucket.cos.ap-beijing.myqcloud.com/mxou-b64/t_abc.png"


# ═══════════════════════════════════════════════════════════════════════
# 1. 既有契约不回归
# ═══════════════════════════════════════════════════════════════════════

def test_non_str_and_empty_contract_kept():
    """非 str / 空串 → True（「非外链即视为本方托管」既有契约，不因收紧外溢）。"""
    assert is_cos_url(None) is True
    assert is_cos_url(123) is True
    assert is_cos_url("") is True
    assert is_cos_url("   ") is True


def test_myqcloud_all_forms_still_pass():
    """区域域 / 全球加速 / 跨区域桶全形态照旧（is_cos_url docstring 既有覆盖面）。"""
    assert is_cos_url(COS_REGION) is True
    assert is_cos_url(COS_ACCELERATE) is True
    assert is_cos_url(COS_BEIJING) is True
    assert is_cos_url(COS_REGION.upper()) is True  # 大写 URL 照旧（内部 lower）


# ═══════════════════════════════════════════════════════════════════════
# 2. 伪装域拒（本批收紧的核心面）
# ═══════════════════════════════════════════════════════════════════════

def test_spoof_mycos_evil_rejected():
    """`mycos.evil.com`：旧裸子串 `cos.` 命中「mycos.」→ 误判本方；现按标签切分拒。"""
    evil = "https://mycos.evil.com/file/images/x.png"
    assert is_cos_url(evil) is False
    assert is_product_image_candidate(evil) is False
    assert filter_product_images([evil]) == []


def test_cos_in_path_or_query_not_judged():
    """裸 `cos.` 嵌在路径/查询串不参与判定（旧实现整条 URL 子串会被垫片绕过）。"""
    assert is_cos_url("https://evil.com/cos.fake/file/images/x.png") is False
    assert is_cos_url("https://evil.com/img/x_cos.1.jpg") is False
    # 查询串垫真 COS URL（旧实现 in 判定直接命中查询串 → 误判本方）
    padded = ("https://evil.com/redirect?to=https://bucket.cos.ap-guangzhou"
              ".myqcloud.com/file/images/a.jpg")
    assert is_cos_url(padded) is False


def test_label_boundary_cos_passes():
    """`cos` 须为完整域标签：cos.accelerate.* / *.cos.* 照旧放行（收紧不误伤）。"""
    assert is_cos_url("https://cos.accelerate.example-internal.com/file/images/a.jpg") is True
    assert is_cos_url("https://cdn.cos.internal.example.com/file/images/a.jpg") is True
    assert is_cos_url("https://cos.example.com/file/images/gen.png") is True  # 测试族常用假域
    # 标签 ≠ 子串：mycos / xcos / cosx 都不算
    assert is_cos_url("https://xcos.example.com/a.jpg") is False
    assert is_cos_url("https://cosx.example.com/a.jpg") is False


def test_scheme_less_host_fail_closed():
    """无 scheme 的裸域串解析不出 hostname → 拒（宁缺毋滥；生产 URL 恒带
    scheme——cos_upload_bytes 生成 `https://` 前缀，消费方调用点均传完整 URL）。"""
    assert is_cos_url("bucket.cos.ap-guangzhou.myqcloud.com/file/images/a.jpg") is False


# ═══════════════════════════════════════════════════════════════════════
# 3. 分类闸联动（本批真正堵住的口子：伪装域 + AI key 前缀曾直判 ai）
# ═══════════════════════════════════════════════════════════════════════

def test_classify_spoof_domain_now_external():
    """伪装域 + AI key 前缀：收紧前 classify=ai / policy 放行，现 external / 拦。"""
    evil = "https://mycos.evil.com/file/images/x.png"
    assert classify_image_source(evil) == "external"
    ok, violations = enforce_upload_policy([evil])
    assert ok is False and violations, "伪装域图必须被上卡 policy 闸拦截"


def test_classify_real_cos_still_ai():
    """本方 COS 真形态：分类闸照旧判 ai（收紧只动域判定，不动 key 通道）。"""
    assert classify_image_source(COS_REGION) == "ai"
    assert classify_image_source(COS_BEIJING) == "ai"
    ok, violations = enforce_upload_policy([COS_REGION])
    assert ok is True and not violations


# ═══════════════════════════════════════════════════════════════════════
# 4. COS_PUBLIC_DOMAIN 自定义公网域通道（cos_upload_bytes 的改写前缀同源）
# ═══════════════════════════════════════════════════════════════════════

def test_public_domain_custom_host_recognized(monkeypatch):
    """配了非 myqcloud 自定义公网域 → 它发出的 URL 必须判本方（否则 AI 图判
    external → 整单 IMAGE_GEN_ALL_FAILED；PLAN-image-ref-cos-whitelist-v1:85
    登记的已知限制随本批收口）。"""
    monkeypatch.setenv("COS_PUBLIC_DOMAIN", "https://img.example-cdn.cn")
    ours = "https://img.example-cdn.cn/file/images/gen.png"
    assert is_cos_url(ours) is True
    assert classify_image_source(ours) == "ai"  # 自定义域 + AI key 前缀 = ai
    assert is_product_image_candidate(ours) is True
    # 子域照旧放行（与 IMAGE_HOST_SUFFIXES 同款 精确/后缀 语义）
    assert is_cos_url("https://a.img.example-cdn.cn/file/images/b.png") is True
    # env 写裸域（无 scheme）同 recognized
    monkeypatch.setenv("COS_PUBLIC_DOMAIN", "img.example-cdn.cn")
    assert is_cos_url(ours) is True


def test_public_domain_not_cached(monkeypatch):
    """env 现读不缓存：摘掉 COS_PUBLIC_DOMAIN 即恢复拒判（测试隔离红线——
    load_dotenv 注入的本地 .env 值不得烘焙进进程）。"""
    ours = "https://img.example-cdn.cn/file/images/gen.png"
    monkeypatch.setenv("COS_PUBLIC_DOMAIN", "https://img.example-cdn.cn")
    assert is_cos_url(ours) is True
    monkeypatch.delenv("COS_PUBLIC_DOMAIN")
    assert is_cos_url(ours) is False


def test_public_domain_empty_default_no_side_effect(monkeypatch):
    """生产默认（env 空）：行为与收紧前逐字一致，自定义域不误开。"""
    monkeypatch.delenv("COS_PUBLIC_DOMAIN", raising=False)
    assert is_cos_url("https://img.example-cdn.cn/file/images/gen.png") is False
