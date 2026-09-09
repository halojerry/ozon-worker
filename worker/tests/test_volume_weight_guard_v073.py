"""v0.73 Task5: 体积-重量密度兜底（0.40 g/cm³）+ ML_INCORRECT_VOLUME_WEIGHT 拒后反推。

Issue4：迷你相机 56g / 83×62×29mm（密度 0.375 g/cm³）被 Ozon ML 拒、原值重发再拒。
生产 199 行留存校准：0.398-0.402 簇全 approved（skill 400 kg/m³ 估算尺寸产物）、
0.375 被拒；approved 里有 0.015 轻抛货 → 守卫只上调（cap 原值×3）永不拒/永不下调。
唯一入口 utils/volume_weight_guard.py（模块注释含生产校准 + 选型说明）。
"""
from utils.volume_weight_guard import (
    MIN_DENSITY_G_CC,
    compute_density_g_cc,
    ensure_volume_weight_floor,
)

CAMERA_DIMS = {"length": 83, "width": 62, "height": 29}  # 149.254 cm³


# ── 纯函数：ensure_volume_weight_floor ──


def test_camera_56g_raised_to_60():
    """锚点用例（Issue4 本体）：0.40×149.254=59.7 → ceil 60。"""
    assert ensure_volume_weight_floor(56, CAMERA_DIMS) == (60, True)


def test_turntable_101g_above_floor_unchanged():
    """生产 approved 转盘实证（64 cm³ → floor 26 < 101）：不调整。"""
    assert ensure_volume_weight_floor(101, {"length": 80, "width": 80, "height": 10}) == (
        101,
        False,
    )


def test_cap_times_three_on_extreme_volume():
    """cap 原值×3 防极端：floor 12800 > 3×2000=6000 → 返回 6000（不到 floor）。"""
    assert ensure_volume_weight_floor(2000, {"length": 400, "width": 400, "height": 200}) == (
        6000,
        True,
    )


def test_ten_gram_contract_floor():
    """10g 契约硬下限：极小体积 density floor 更低时仍抬到 10g（不受 cap 限制）。"""
    # 20×15×10mm = 3 cm³ → density floor 2 < 10 → target 10；min(10, max(15,10))=10
    assert ensure_volume_weight_floor(5, {"length": 20, "width": 15, "height": 10}) == (
        10,
        True,
    )
    # w=1 → cap 3 < 10 → 仍保底 10（契约下限不是「极端抬升」）
    assert ensure_volume_weight_floor(1, {"length": 20, "width": 15, "height": 10}) == (
        10,
        True,
    )


def test_never_downgrades():
    """永不下调：重量远超 floor 时原样返回。"""
    assert ensure_volume_weight_floor(5000, CAMERA_DIMS) == (5000, False)
    assert ensure_volume_weight_floor(100, {"length": 20, "width": 15, "height": 10}) == (
        100,
        False,
    )


def test_invalid_inputs_returned_as_is():
    """weight<=0 / dims 缺失或非法 → 原样返回（不猜）。"""
    assert ensure_volume_weight_floor(0, CAMERA_DIMS) == (0, False)
    assert ensure_volume_weight_floor(-5, CAMERA_DIMS) == (-5, False)
    assert ensure_volume_weight_floor(56, {}) == (56, False)
    assert ensure_volume_weight_floor(56, None) == (56, False)
    assert ensure_volume_weight_floor(56, {"length": 83}) == (56, False)
    assert ensure_volume_weight_floor(56, {"length": 0, "width": 62, "height": 29}) == (
        56,
        False,
    )
    assert ensure_volume_weight_floor(None, CAMERA_DIMS) == (None, False)
    assert ensure_volume_weight_floor("abc", CAMERA_DIMS) == ("abc", False)


def test_string_weight_accepted():
    """payload 重量是字符串（retry 侧 item['weight']）→ 数值化处理。"""
    assert ensure_volume_weight_floor("56", CAMERA_DIMS) == (60, True)


def test_min_density_constant_calibrated():
    """常量校准锚点：禁止悄悄改阈值（生产 199 行校准值）。"""
    assert MIN_DENSITY_G_CC == 0.40


def test_compute_density_helper():
    """密度留痕 helper：相机 0.375；非法输入 None。"""
    assert compute_density_g_cc(56, CAMERA_DIMS) == 0.375
    assert compute_density_g_cc(0, CAMERA_DIMS) is None
    assert compute_density_g_cc(56, {}) is None


# ── prepare 接线：_resolve_weight_dimensions（normalizer 后调 guard + marks）──


def test_prepare_wiring_raises_and_marks():
    """prepare 接线：56g 相机 → 60g 定稿 + marks 留痕（_wd_audit 消费同源）。"""
    from graphs.nodes.prepare_ozon_upload_node import _resolve_weight_dimensions

    draft = {"weight": 56, "dimensions": dict(CAMERA_DIMS)}
    weight_g, d, w, h = _resolve_weight_dimensions(draft, {})
    assert (weight_g, d, w, h) == (60, 83, 62, 29)
    marks = _resolve_weight_dimensions._wd_marks
    assert marks["weight_adjusted_for_volume"] == {
        "from": 56,
        "to": 60,
        "density_before": 0.375,
    }
    assert any("weight_raised_for_volume" in r for r in marks["reasons"])


def test_prepare_wiring_no_mark_when_untouched():
    """正常密度 → 重量不动、无 weight_adjusted_for_volume 键（省略键语义）。"""
    from graphs.nodes.prepare_ozon_upload_node import _resolve_weight_dimensions

    draft = {"weight": 101, "dimensions": {"length": 80, "width": 80, "height": 10}}
    weight_g, *_ = _resolve_weight_dimensions(draft, {})
    assert weight_g == 101
    assert "weight_adjusted_for_volume" not in _resolve_weight_dimensions._wd_marks


def test_prepare_wiring_low_density_real_value_not_flagged_by_normalizer():
    """调查结论锚点：0.375 g/cm³=375 kg/m³ 高于 normalizer 标疑线 1.293 kg/m³
    → dimensions_suspected 恒 False（此前连标疑都没有，56g 静默穿过四道防线）。"""
    from graphs.nodes.prepare_ozon_upload_node import _resolve_weight_dimensions

    draft = {"weight": 56, "dimensions": dict(CAMERA_DIMS)}
    _resolve_weight_dimensions(draft, {})
    marks = _resolve_weight_dimensions._wd_marks
    assert marks["dimensions_suspected"] is False
    assert marks["weight_source"] == "draft"  # 真实值，normalizer 不改写


# ── retry 接线：repair_dimensions_node 的 ML_INCORRECT_VOLUME_WEIGHT 分支 ──


def _retry_state(weight, dims, error_code="ML_INCORRECT_VOLUME_WEIGHT", errors=None):
    from graphs.validation_retry_loop import ValidationRetryLoopState

    return ValidationRetryLoopState(
        ozon_payload={"items": [{
            "name": "Товар", "weight": str(weight), "weight_unit": "g",
            "depth": str(dims.get("length", 0)), "width": str(dims.get("width", 0)),
            "height": str(dims.get("height", 0)), "dimension_unit": "mm",
            "offer_id": "x", "price": "10", "old_price": "12", "currency_code": "CNY",
        }]},
        error_code=error_code,
        errors=errors or [],
        retry_count=1,
    )


def test_retry_ml_error_raises_weight_via_guard():
    """拒后反推：56g 相机被 ML 拒 → 60g 重发 + repair_marks 留痕。"""
    from graphs.validation_retry_loop import repair_dimensions_node

    out = repair_dimensions_node(_retry_state(56, CAMERA_DIMS))
    item = out.ozon_payload["items"][0]
    assert item["weight"] == "60"
    applied = (out.repair_marks or {}).get("weight_floor_applied", [])
    assert applied and applied[0]["from_g"] == 56 and applied[0]["to_g"] == 60
    assert applied[0]["density_before"] == 0.375


def test_retry_above_floor_keeps_existing_behavior():
    """已在 MIN_DENSITY 之上仍被拒 → 不盲改（重量原样，无 weight_floor_applied）。"""
    from graphs.validation_retry_loop import repair_dimensions_node

    out = repair_dimensions_node(_retry_state(387, {"length": 115, "width": 32, "height": 115}))
    item = out.ozon_payload["items"][0]
    assert item["weight"] == "387"
    assert not (out.repair_marks or {}).get("weight_floor_applied")


def test_retry_other_error_code_no_guard():
    """非 ML_INCORRECT_VOLUME_WEIGHT（如属性错被误路由）→ guard 不动重量。"""
    from graphs.validation_retry_loop import repair_dimensions_node

    out = repair_dimensions_node(_retry_state(56, CAMERA_DIMS, error_code="INVALID_PRICE"))
    assert out.ozon_payload["items"][0]["weight"] == "56"
    assert not (out.repair_marks or {}).get("weight_floor_applied")


def test_retry_guard_via_remaining_errors_queue():
    """errors 剩余队列里带该 code（非本轮 error_code）也触发。"""
    from graphs.validation_retry_loop import repair_dimensions_node

    out = repair_dimensions_node(
        _retry_state(56, CAMERA_DIMS, error_code="UNKNOWN",
                     errors=[{"code": "ML_INCORRECT_VOLUME_WEIGHT", "attribute_id": 0}])
    )
    assert out.ozon_payload["items"][0]["weight"] == "60"


def test_retry_v069_gate_takes_precedence_no_double_adjust():
    """v0.69 50 kg/m³ 闸保留不动且优先：密度<50 → 300 kg/m³ 反推接管本轮，
    guard 不叠加（300 kg/m³=0.3 g/cc < 0.40 也不二次抬）。"""
    from graphs.validation_retry_loop import repair_dimensions_node

    # 300g / 330×430×100mm = 14190 cm³，密度 21 kg/m³ < 50 → 反推 4257g
    out = repair_dimensions_node(_retry_state(300, {"length": 330, "width": 430, "height": 100}))
    item = out.ozon_payload["items"][0]
    assert item["weight"] == "4257"
    marks = out.repair_marks or {}
    assert marks["weight_inferred"][0]["to_g"] == 4257
    assert not marks.get("weight_floor_applied")  # 同一轮不叠加
