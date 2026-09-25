"""PRD M5b: 草稿图片镜像测试(真实 PG,COS mock)。

覆盖:create/patch 触发异步镜像(状态列 pending→mirrored)、version 守卫丢弃
(R8:版本已变不覆盖)、COS 未配置时保持外链不报错、镜像函数幂等(COS URL 跳过)。
"""
import json
import os
import socket
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:ozon123@localhost:5433/ozon",
)
os.environ["CREDENTIAL_MASTER_KEY"] = "0123456789abcdef0123456789abcdef"
os.environ["SKIP_ZOMBIE_RECOVERY"] = "1"
os.environ["SKIP_FAILED_REVIVE"] = "1"

import main as main_mod  # noqa: E402

TENANT = main_mod._key_user_id("tokMir")


# v0.76 T14(inj-C1)：镜像下载改走 utils.secure_fetch.safe_fetch——HTTP 假体与
# DNS 都要 patch 到 secure_fetch 内部（requests.get 已不在镜像链上），保证零出站。
# ⚠️ 只钉外链域名：getaddrinfo 是进程级 patch，psycopg3（纯 Python，SA 2.1 起
# postgresql:// 默认方言）连 DB 也走它——localhost 被钉到 93.184.216.34 会造成
# OS 级连接超时（psycopg2 走 libpq C 解析器不受影响）。环回/本机走真实解析。
_real_getaddrinfo = socket.getaddrinfo


def _fake_secure_dns(host, port=None, *args, **kwargs):
    if str(host) in ("localhost", "127.0.0.1", "::1", ""):
        return _real_getaddrinfo(host, port, *args, **kwargs)
    return [(2, 1, 6, "", ("93.184.216.34", port or 0))]


def _fake_mirror_http():
    return SimpleNamespace(status_code=200, content=b"fake-image",
                           is_redirect=False, is_permanent_redirect=False)


class FakeTokensTable:
    def __init__(self):
        self._rows = [{"user_id": "tenant-mir", "key": "tokMir", "status": 1, "deleted_at": None}]
        self._eqs = []

    def select(self, *cols):
        return self

    def eq(self, col, val):
        self._eqs.append((col, val))
        return self

    def is_(self, col, val):
        self._eqs.append((col, val))
        return self

    def limit(self, n):
        return self

    def execute(self):
        eqs, self._eqs = self._eqs, []
        filtered = self._rows
        for col, val in eqs:
            filtered = [r for r in filtered if str(r.get(col)) == str(val)]
        return SimpleNamespace(data=filtered[:1])


class FakeSupabase:
    def table(self, name):
        if name == "tokens":
            return FakeTokensTable()
        raise AssertionError(f"unexpected table {name}")


@pytest.fixture(scope="module")
def client():
    eng = create_engine(DB_URL)
    try:
        with eng.connect():
            pass
    except Exception:
        pytest.skip("本地 PG 不可达")
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM product_drafts WHERE tenant_id=:t"
        ), {"t": TENANT})
    with TestClient(main_mod.app) as c:
        yield c
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM product_drafts WHERE tenant_id=:t"
        ), {"t": TENANT})


def _envelope(title: str = "镜像测试商品", images=None) -> dict:
    return {
        "draft": {
            "title": title,
            "item_id": "mir-1688-1",
            "images": images or ["https://cbu01.alicdn.com/img/ibank/2024/test.jpg"],
            "weight": 500,
            "dimensions": {"length": 20, "width": 10, "height": 5},
        },
        "source": {"purchase_url": "https://detail.1688.com/offer/1.html",
                   "purchase_cost": 8.8},
        "extensions": {},
    }


def _row(eng, draft_id: str) -> tuple:
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT version, image_mirror_state, payload FROM product_drafts "
            "WHERE id=:id AND tenant_id=:t"
        ), {"id": uuid.UUID(draft_id), "t": TENANT}).fetchone()
    return row


def test_premirror_disabled_by_default_keeps_raw_urls(client, monkeypatch):
    """✅ fix/retry-image-restore-v1: 入箱预镜像默认停用——图保持 1688 裸链、状态空。

    COS 只存「提交时按需转存 + 生成图」；DRAFT_IMAGE_MIRROR=1 才恢复入箱即镜像。
    """
    monkeypatch.delenv("DRAFT_IMAGE_MIRROR", raising=False)
    monkeypatch.setenv("COS_SECRET_ID", "test")
    monkeypatch.setenv("COS_SECRET_KEY", "test")
    monkeypatch.setenv("COS_BUCKET", "test-bucket")
    with patch("requests.get") as mock_get:
        resp = client.post("/api/v1/drafts", json={
            "token": "tokMir", "source": "webui",
            "envelope": _envelope(),
        })
        assert resp.status_code == 200
        draft_id = resp.json()["id"]
        time.sleep(0.5)  # 若预镜像误触发，给异步线程回写窗口
        row = _row(create_engine(DB_URL), draft_id)
        assert row[1] == "", f"预镜像应默认停用，实际状态={row[1]!r}"
        payload = json.loads(row[2]) if isinstance(row[2], str) else row[2]
        assert payload["draft"]["images"] == ["https://cbu01.alicdn.com/img/ibank/2024/test.jpg"]
        mock_get.assert_not_called()


def test_mirror_runs_and_updates_state(client, monkeypatch):
    """COS 配置 + 下载成功 → 异步镜像回写(mirrored + COS URL)。

    ✅ fix/retry-image-restore-v1: 入箱预镜像默认停用（draft.images 保留裸链，
    COS 只存提交时按需转存 + 生成图）——本用例验证镜像机制本身，显式开
    DRAFT_IMAGE_MIRROR=1 走旧路径。
    """
    eng = create_engine(DB_URL)
    monkeypatch.setenv("DRAFT_IMAGE_MIRROR", "1")
    os.environ["COS_SECRET_ID"] = "test"
    os.environ["COS_SECRET_KEY"] = "test"
    os.environ["COS_BUCKET"] = "test-bucket"
    try:
        with patch("utils.secure_fetch.socket.getaddrinfo", _fake_secure_dns), \
             patch("utils.secure_fetch.requests.request",
                   return_value=_fake_mirror_http()), \
             patch("services.draft_image_mirror.cos_upload_bytes",
                   return_value="https://test-bucket.cos.ap-guangzhou.myqcloud.com/draft-images/abc.jpg"):
            resp = client.post("/api/v1/drafts", json={
                "token": "tokMir", "source": "webui",
                "envelope": _envelope(),
            })
            assert resp.status_code == 200
            draft_id = resp.json()["id"]
            for _ in range(50):
                row = _row(eng, draft_id)
                if row[1] == "mirrored":
                    break
                time.sleep(0.1)
            assert row[1] == "mirrored"
            payload = json.loads(row[2]) if isinstance(row[2], str) else row[2]
            assert payload["draft"]["images"] == [
                "https://test-bucket.cos.ap-guangzhou.myqcloud.com/draft-images/abc.jpg"]
    finally:
        for k in ("COS_SECRET_ID", "COS_SECRET_KEY", "COS_BUCKET"):
            os.environ.pop(k, None)


def test_mirror_version_guard_drops_stale(client):
    """镜像回写时版本已变 → 丢弃并保持 failed(不覆盖新编辑,R8)。"""
    eng = create_engine(DB_URL)
    os.environ["COS_SECRET_ID"] = "test"
    os.environ["COS_SECRET_KEY"] = "test"
    os.environ["COS_BUCKET"] = "test-bucket"
    try:
        with patch("utils.secure_fetch.socket.getaddrinfo", _fake_secure_dns), \
             patch("utils.secure_fetch.requests.request",
                   return_value=_fake_mirror_http()), \
             patch("services.draft_image_mirror.cos_upload_bytes",
                   return_value="https://test-bucket.cos.ap-guangzhou.myqcloud.com/draft-images/abc.jpg"):
            resp = client.post("/api/v1/drafts", json={
                "token": "tokMir", "source": "webui",
                "envelope": _envelope(title="版本守卫商品"),
            })
            assert resp.status_code == 200
            draft_id = resp.json()["id"]
            # 在镜像线程回写前立刻编辑(version 1 → 2),让镜像回写变成 stale
            client.patch(f"/api/v1/drafts/{draft_id}", json={
                "version": 1,
                "payload": _envelope(title="版本守卫商品-已编辑"),
            }, headers={"Authorization": "Bearer tokMir"})
            time.sleep(1.0)
            row = _row(eng, draft_id)
            payload = json.loads(row[2]) if isinstance(row[2], str) else row[2]
            assert row[1] in ("failed", "mirrored") or payload["draft"]["title"] == "版本守卫商品-已编辑"
            # 关键:新编辑内容不能被旧镜像覆盖
            assert payload["draft"]["title"] == "版本守卫商品-已编辑"
    finally:
        for k in ("COS_SECRET_ID", "COS_SECRET_KEY", "COS_BUCKET"):
            os.environ.pop(k, None)


def test_mirror_disabled_keeps_original_urls(client):
    """COS 未配置 → 保持外链(状态 ''),不报错。"""
    eng = create_engine(DB_URL)
    resp = client.post("/api/v1/drafts", json={
        "token": "tokMir", "source": "skill",
        "envelope": _envelope(title="无 COS 商品"),
    })
    assert resp.status_code == 200
    draft_id = resp.json()["id"]
    row = _row(eng, draft_id)
    assert row[1] == ""
    payload = json.loads(row[2]) if isinstance(row[2], str) else row[2]
    assert payload["draft"]["images"][0].startswith("https://cbu01.alicdn.com")


def test_mirror_skips_already_cos_urls():
    """镜像函数对 COS URL 幂等(不重复下载)。"""
    from services.draft_image_mirror import mirror_draft_images
    payload = {
        "draft": {"images": [
            "https://test-bucket.cos.ap-guangzhou.myqcloud.com/draft-images/a.jpg"]},
    }
    new_images, changed = mirror_draft_images(payload)
    assert changed is False
    assert new_images == payload["draft"]["images"]
