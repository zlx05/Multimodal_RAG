"""Safe URLs for original documents and extracted document assets.

原始文档与解析产物的访问统一走**签名临时 URL**：

- `original_url` / `asset_url` 生成的链接带 `exp`（过期时间戳）与 `sign`
  （HMAC-SHA256 签名），由服务端生成、只出现在鉴权后的接口响应里；
- 文件服务接口（`<img>` / `<iframe>` / 链接点击）用不了 Bearer header，
  因此用签名 URL 代替 header 鉴权，避免未鉴权方猜测资源路径直接拉取文件。

签名密钥复用 JWT 的 `config.SECRET_KEY`（资源串带 `original:` / `asset:` 域名前缀，
与 JWT 用途隔离）。与 JWT 同一条信任链：若部署连 SECRET_KEY 都没改，
整个鉴权本就不可信，资源签名弱化不是单独的风险面。
"""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
import time
from pathlib import Path
from urllib.parse import quote

from ..core import config
from ..core.config import DATA_DIR

DATA_ROOT = DATA_DIR.resolve()

# 签名临时 URL 有效期（秒）。到期后前端需重新从接口获取新 URL（每个响应都会重取）。
ASSET_URL_TTL = 3600


def _sign(resource: str, exp: int) -> str:
    message = f"{resource}:{exp}".encode("utf-8")
    return hmac.new(str(config.SECRET_KEY).encode("utf-8"), message, hashlib.sha256).hexdigest()


def _signed(path: str, resource: str) -> str:
    exp = int(time.time()) + ASSET_URL_TTL
    return f"{path}?exp={exp}&sign={_sign(resource, exp)}"


def original_url(document_id: str) -> str:
    path = f"/api/v1/documents/{quote(document_id, safe='')}/original"
    return _signed(path, f"original:{document_id}")


def asset_url(document_id: str, image_path: str | None) -> str | None:
    if not image_path:
        return None
    try:
        path = Path(image_path).resolve()
        relative = path.relative_to(DATA_ROOT).as_posix()
    except (OSError, ValueError):
        return None
    # Every extracted asset is stored under a document-specific path. This
    # prevents an arbitrary path from becoming a file-serving URL.
    if document_id not in path.parts and not path.name.startswith(document_id):
        return None
    path_url = f"/api/v1/documents/{quote(document_id, safe='')}/assets/{quote(relative, safe='/')}"
    return _signed(path_url, f"asset:{document_id}:{relative}")


def verify_asset_signature(
    resource_type: str,
    document_id: str,
    asset_path: str | None,
    exp: int,
    sign: str,
) -> bool:
    """校验签名临时 URL。resource 构造与 URL 生成侧完全一致，exp 超时即拒绝。

    用 `hmac.compare_digest` 做常数时间比较，避免时序侧信道。
    """
    resource = f"{resource_type}:{document_id}"
    if asset_path:
        resource += f":{asset_path}"
    if not sign or exp < int(time.time()):
        return False
    return hmac.compare_digest(sign, _sign(resource, exp))


def media_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"
