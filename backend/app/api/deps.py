"""身份依赖（Phase 1.1 真实鉴权：密码/JWT）。

`Authorization: Bearer <JWT>` 承载身份。token 由 `/api/v1/auth/login`（或
`/setup-password`）签发，HS256 签名，`scope=access` 才是正式身份。

- 无 Authorization 头 / 非 Bearer / token 无效或过期 / scope 不符 → 401。
- role 以数据库最新值为准（token 里的 role 仅作 DB 挂时的降级快照）。
- MySQL 不可用时：**已验签** token → 用 claims 合成降级身份（degraded=True，
  可继续个人检索问答，但 require_admin 会 503 拒绝，不会升级成管理员权限）；
  数据库查询失败抛出的其他场景维持 fail-closed。
- 用户被删除 → 401（与"无效 token"统一，避免探测账号存在性）。
"""

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError

from ..db import org
from ..core import security

__all__ = [
    "get_current_user",
    "require_admin",
    "require_head",
    "require_original_signature",
    "require_asset_signature",
]


def get_current_user(authorization: str | None = Header(default=None)) -> dict:
    """解析 Bearer token 中的当前用户。Header 缺失 / token 无效 → 401。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少 Bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = security.decode_access_token(token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="无效或已过期的 token") from exc
    if payload.get("scope") != "access":
        raise HTTPException(status_code=401, detail="token 用途不正确")
    user_id = payload["sub"]
    try:
        user = org.get_user(user_id)  # role 以 DB 最新值为准
    except SQLAlchemyError as exc:
        print(f"[deps] 数据库不可用，按 token 快照降级 {user_id}：{exc}")
        return {
            "id": user_id,
            "username": payload.get("username", ""),
            "role": payload.get("role", "member"),
            "degraded": True,  # 已验签但未复核 DB 的合成身份：禁止管理操作
        }
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在或已删除")
    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """检查角色，老师(admin)或班主任(head)放行；降级合成身份一律 503。

    依赖 get_current_user 注入身份（不是请求体字段），管理员端点经此校验。
    """
    if user.get("degraded"):
        raise HTTPException(status_code=503, detail="数据库不可用，无法校验管理员身份")
    if user.get("role") not in {"admin", "head"}:
        raise HTTPException(status_code=403, detail="仅管理员可执行此操作")
    return user


def require_head(user: dict = Depends(get_current_user)) -> dict:
    """班主任专属依赖：只有 head 能建/删老师、管理班级级操作。"""
    if user.get("degraded"):
        raise HTTPException(status_code=503, detail="数据库不可用，无法校验班主任身份")
    if user.get("role") != "head":
        raise HTTPException(status_code=403, detail="仅班主任可执行此操作")
    return user


def require_original_signature(
    document_id: str,
    exp: int = Query(default=0),
    sign: str = Query(default=""),
) -> None:
    """原始资料文件必须携带有效的签名临时 URL。

    `<img>` / `<iframe>` / 链接点击发不了 Bearer header，所以文件服务接口
    用签名 URL（assets.original_url 生成）代替 header 鉴权。document_id 由
    路径参数注入，与生成侧 `original:<id>` 一致。
    """
    from ..rag.assets import verify_asset_signature

    if not verify_asset_signature("original", document_id, None, exp, sign):
        raise HTTPException(status_code=403, detail="资源链接无效或已过期")


def require_asset_signature(
    document_id: str,
    asset_path: str,
    exp: int = Query(default=0),
    sign: str = Query(default=""),
) -> None:
    """解析产物（图片/附件）必须携带有效的签名临时 URL。

    asset_path 是签名时用的相对路径（FastAPI 解码 path 参数后与生成侧一致），
    签名绑定 `asset:<document_id>:<asset_path>`，不能跨资源复用。
    """
    from ..rag.assets import verify_asset_signature

    if not verify_asset_signature("asset", document_id, asset_path, exp, sign):
        raise HTTPException(status_code=403, detail="资源链接无效或已过期")
