"""密码哈希与 JWT 签发/验签。

Phase 1.1 真实鉴权：bcrypt 存密码哈希，PyJWT（HS256）签 access token。
- bcrypt 输入上限 72 字节，超长会被静默截断；配合 Pydantic max_length=64 兜底。
- `config.SECRET_KEY` 在调用时读取（不在 import 时快照），测试才能设 env/monkeypatch。
"""

import time

import bcrypt
import jwt

from ..core import config

ALGORITHM = "HS256"
# bcrypt 输入上限 72 字节；密码最长 64 字符（中文字符 1 字符≈3 字节，最多 192 字节，
# 但加解密两端同样截断，验证正确性不受影响，仅有效强度覆盖前 72 字节）。
MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8")[:MAX_PASSWORD_BYTES], bcrypt.gensalt()
    ).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(
            password.encode("utf-8")[:MAX_PASSWORD_BYTES],
            password_hash.encode("utf-8"),
        )
    except ValueError:
        return False


def create_access_token(
    user_id: str,
    role: str,
    username: str = "",
    scope: str = "access",
    expires_minutes: int | None = None,
) -> str:
    """签发 JWT。scope=access 正式身份 / scope=setup 引导式补设的短效专用令牌。"""
    now = int(time.time())
    minutes = expires_minutes if expires_minutes is not None else config.ACCESS_TOKEN_EXPIRE_MINUTES
    payload = {
        "sub": user_id,
        "role": role,
        "username": username,
        "scope": scope,
        "iat": now,
        "exp": now + minutes * 60,
    }
    return jwt.encode(payload, config.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """验签 + 验 exp + 限制算法。任何失败抛 jwt.InvalidTokenError 子类。"""
    return jwt.decode(token, config.SECRET_KEY, algorithms=[ALGORITHM])
