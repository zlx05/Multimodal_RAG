"""鉴权测试助手：直接签发/伪造 Bearer token，避免测试逐个走 login 流程。

核心思路：测试身份不经过密码流程，直接调 `security.create_access_token` 生成 token，
与 deps 的验签逻辑解耦（deps 只认 token 里的 sub/role/scope）。
"""

import jwt

from backend.app.core import security
from backend.app.db.seed import DEFAULT_ADMIN_ID


def make_token(user_id: str, role: str = "member", username: str = "",
               scope: str = "access", expires_minutes: int = 1440) -> str:
    return security.create_access_token(
        user_id=user_id,
        role=role,
        username=username or user_id,
        scope=scope,
        expires_minutes=expires_minutes,
    )


def auth_headers(user_id: str, role: str = "member", scope: str = "access",
                 expires_minutes: int = 1440) -> dict:
    return {"Authorization": f"Bearer {make_token(user_id, role, user_id, scope, expires_minutes)}"}


def tampered_token(user_id: str = "u_ghost", role: str = "head") -> str:
    """用错误密钥签发的伪造 token（模拟篡改/伪造）。"""
    return jwt.encode(
        {"sub": user_id, "role": role, "scope": "access"},
        "wrong-secret-for-tests",
        algorithm="HS256",
    )


def as_admin() -> dict:
    """u_admin（head）身份的 Bearer header。"""
    return auth_headers(DEFAULT_ADMIN_ID, "head")
