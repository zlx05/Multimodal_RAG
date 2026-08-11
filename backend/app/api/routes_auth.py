"""认证端点（Phase 1.1 真实鉴权）。

- `POST /auth/login`：用户名+密码 → access token。账号从未设置密码时返回
  `needs_password_setup=true` + 短效 `setup_token`（引导式补设），不发正式 token。
- `POST /auth/setup-password`：用 setup_token 设密码 → 正式 access token。
  若账号已设过密码（有人抢先设了）→ 409，本 setup token 作废（后到者失败）。
- `POST /auth/change-password`：需正式 access token，验证旧密码后改新密码。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core import config, security
from ..db import org
from .deps import get_current_user

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _public(cred: dict) -> dict:
    """去掉 password_hash，安全返回用户信息。"""
    return {k: v for k, v in cred.items() if k != "password_hash"}


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=64)


class SetupPasswordRequest(BaseModel):
    setup_token: str = Field(min_length=1)
    password: str = Field(min_length=6, max_length=64)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=64)
    new_password: str = Field(min_length=6, max_length=64)


@router.post("/login")
def login(body: LoginRequest):
    username = body.username.strip()
    cred = org.get_credentials_by_username(username)
    if cred is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if cred["password_hash"] is None:
        # 首次登录：引导式补设。发 scope=setup 短效 token，不发正式 token。
        setup_token = security.create_access_token(
            cred["id"],
            cred["role"],
            cred["username"],
            scope="setup",
            expires_minutes=config.SETUP_TOKEN_EXPIRE_MINUTES,
        )
        return {
            "needs_password_setup": True,
            "setup_token": setup_token,
            "user": _public(cred),
        }
    if not security.verify_password(body.password, cred["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    access_token = security.create_access_token(
        cred["id"],
        cred["role"],
        cred["username"],
        scope="access",
        expires_minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES,
    )
    return {"access_token": access_token, "token_type": "bearer", "user": _public(cred)}


@router.post("/setup-password")
def setup_password(body: SetupPasswordRequest):
    try:
        payload = security.decode_access_token(body.setup_token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="无效或已过期的设置密码令牌") from exc
    if payload.get("scope") != "setup":
        raise HTTPException(status_code=401, detail="令牌用途不正确")
    user_id = payload["sub"]
    cred = org.get_credentials(user_id)
    if cred is None:
        raise HTTPException(status_code=401, detail="用户不存在或已删除")
    if cred["password_hash"] is not None:
        # 已有人设过密码 → 本 setup token 作废，防止"谁先设谁拥有"被抢先利用。
        raise HTTPException(status_code=409, detail="该账号已设置过密码，请直接登录")
    org.set_password(user_id, security.hash_password(body.password))
    access_token = security.create_access_token(
        cred["id"],
        cred["role"],
        cred["username"],
        scope="access",
        expires_minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES,
    )
    return {"access_token": access_token, "token_type": "bearer", "user": _public(cred)}


@router.post("/change-password")
def change_password(body: ChangePasswordRequest, current_user: dict = Depends(get_current_user)):
    cred = org.get_credentials(current_user["id"])
    if cred is None or cred["password_hash"] is None:
        raise HTTPException(status_code=400, detail="该账号尚未设置密码")
    if not security.verify_password(body.old_password, cred["password_hash"]):
        raise HTTPException(status_code=400, detail="原密码不正确")
    org.set_password(current_user["id"], security.hash_password(body.new_password))
    return {"ok": True}
