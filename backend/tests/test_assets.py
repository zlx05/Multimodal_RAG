"""签名临时 URL 的纯函数 + FastAPI 依赖注入测试。

覆盖：URL 结构、签名与独立 HMAC 一致、过期/篡改/跨资源复用被拒、
asset_url 相对路径绑定，以及 FastAPI 能把路径参数注入 require_*_signature。
"""

import hashlib
import hmac

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.app.rag.assets import asset_url, original_url, verify_asset_signature
from backend.app.api.deps import require_asset_signature, require_original_signature

SECRET = "test-secret"


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr("backend.app.core.config.SECRET_KEY", SECRET)


def _hmac_sign(resource: str, exp: int) -> str:
    message = f"{resource}:{exp}".encode("utf-8")
    return hmac.new(SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _params(url: str) -> dict[str, str]:
    _, query = url.split("?", 1)
    return dict(pair.split("=", 1) for pair in query.split("&"))


# ---------------- URL 生成 ----------------

def test_original_url_signed_and_verifies():
    url = original_url("doc_abc")
    assert url.startswith("/api/v1/documents/doc_abc/original?exp=")
    p = _params(url)
    assert verify_asset_signature("original", "doc_abc", None, int(p["exp"]), p["sign"])


def test_original_signature_matches_independent_hmac():
    p = _params(original_url("doc_abc"))
    assert p["sign"] == _hmac_sign("original:doc_abc", int(p["exp"]))


def test_asset_url_signed_and_bound_to_relative_path(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.app.rag.assets.DATA_ROOT", tmp_path)
    image = tmp_path / "doc_abc" / "p1.png"
    url = asset_url("doc_abc", str(image))
    assert url is not None
    assert url.startswith("/api/v1/documents/doc_abc/assets/doc_abc/p1.png?exp=")
    p = _params(url)
    # 相对路径是签名资源的一部分
    assert verify_asset_signature("asset", "doc_abc", "doc_abc/p1.png", int(p["exp"]), p["sign"])
    # 换成别的相对路径 → 同一个 URL 的签名对不上
    assert not verify_asset_signature("asset", "doc_abc", "doc_abc/p2.png", int(p["exp"]), p["sign"])


def test_asset_url_none_without_image():
    assert asset_url("doc_abc", None) is None


def test_asset_url_rejects_unrelated_path(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.app.rag.assets.DATA_ROOT", tmp_path)
    # document_id 不在路径中 → 不生成 URL（防路径穿越）
    assert asset_url("doc_abc", str(tmp_path / "other" / "x.png")) is None


# ---------------- 校验 ----------------

def test_verify_rejects_wrong_signature():
    assert not verify_asset_signature("original", "doc_abc", None, 9999999999, "deadbeef")


def test_verify_rejects_expired():
    sign = _hmac_sign("original:doc_abc", 1000)  # exp=1000 早已过期，但签名本身正确
    assert not verify_asset_signature("original", "doc_abc", None, 1000, sign)


def test_verify_rejects_empty_signature():
    assert not verify_asset_signature("original", "doc_abc", None, 9999999999, "")


def test_signature_bound_to_document():
    # 用 doc_abc 的签名访问 doc_xyz → 拒绝（签名绑定资源，不能跨文档复用）
    sign = _hmac_sign("original:doc_abc", 9999999999)
    assert not verify_asset_signature("original", "doc_xyz", None, 9999999999, sign)


# ---------------- FastAPI 依赖注入 ----------------

def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/v1/documents/{document_id}/original")
    def original(document_id: str, _guard: None = Depends(require_original_signature)):
        return {"ok": True}

    @app.get("/api/v1/documents/{document_id}/assets/{asset_path:path}")
    def asset(document_id: str, asset_path: str, _guard: None = Depends(require_asset_signature)):
        return {"ok": True}

    return app


def _client() -> TestClient:
    return TestClient(_make_app())


def test_original_endpoint_accepts_valid_signature():
    url = original_url("doc_abc")
    path = url.split("?")[0]
    resp = _client().get(f"{path}?{url.split('?')[1]}")
    assert resp.status_code == 200


def test_original_endpoint_rejects_missing_signature():
    resp = _client().get("/api/v1/documents/doc_abc/original")
    assert resp.status_code == 403


def test_original_endpoint_rejects_expired():
    url = original_url("doc_abc")
    p = _params(url)
    # 把 exp 改成过去时间，签名不变 → 过期拒绝
    resp = _client().get(f"/api/v1/documents/doc_abc/original?exp=1000&sign={p['sign']}")
    assert resp.status_code == 403


def test_asset_endpoint_injects_asset_path_into_guard(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.app.rag.assets.DATA_ROOT", tmp_path)
    image = tmp_path / "doc_abc" / "p1.png"
    url = asset_url("doc_abc", str(image))
    assert url is not None
    path, _, query = url.partition("?")
    assert _client().get(f"{path}?{query}").status_code == 200
    # 路径里换文件 → 签名不匹配
    wrong = path.replace("p1.png", "p2.png")
    assert _client().get(f"{wrong}?{query}").status_code == 403
