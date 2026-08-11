"""Phase 1.1 真实鉴权 e2e 冒烟（一次性 uvicorn 实例，不碰共享 MySQL/Milvus/Redis）。

隔离方式：临时 SQLite 文件 + 临时端口 8511 + 只挂 auth/org 两个 router，
并把 org/seed 的 session factory 换成 SQLite（与 pytest 同款做法）。
TaskStore 的 Redis client 是惰性的（首条命令才连接），本流程不触发连接。

跑法（仓库根目录）：
  D:/mnist_data/ancanda/envs/rag11/python.exe scripts/smoke_auth_e2e.py
"""

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

# 仓库根目录（scripts/ 下运行时 sys.path[0] 是 scripts/，需手动补）。
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

# 必须在 import backend 之前设置（config/security 在 import 时读取）。
os.environ["JWT_SECRET_KEY"] = "e2e-secret-for-smoke-test"
os.environ["JWT_ACCESS_TOKEN_EXPIRE_MINUTES"] = "10080"
os.environ["JWT_SETUP_TOKEN_EXPIRE_MINUTES"] = "15"

import uvicorn
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.routes_auth import router as auth_router
from backend.app.api.routes_org import router as org_router
from backend.app.core.database import Base
from backend.app.db import org, seed as seed_module
from backend.app.db.seed import seed_default_admin

DB_PATH = os.path.abspath(os.path.join("data", ".work", "e2e_auth.db"))

BASE = "http://127.0.0.1:8511"
passed: list[str] = []
failed: list[str] = []


def req(method: str, path: str, body: dict | None = None, token: str | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read().decode())
        except Exception:
            return err.code, {}


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(f"{name}  {detail}" if not cond else name)


def main() -> int:
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    # 与 pytest 同款隔离：SQLite 文件 + StaticPool，替换 org/seed 的 factory，
    # 保证 uvicorn 的请求线程与主线程共享同一个连接。
    engine = create_engine(
        f"sqlite:///{DB_PATH}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    org._session_factory = Session
    seed_module._session_factory = Session  # seed 用自己的 factory，需一并替换
    seed_default_admin()

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(org_router)
    config = uvicorn.Config(app, host="127.0.0.1", port=8511, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    time.sleep(2.0)

    try:
        # 1) 无 header → 401（不再回退 u_admin）
        status, _ = req("GET", "/api/v1/users/me")
        check("无 header 401", status == 401, f"status={status}")

        # 2) u_admin 首登 → 引导式补设（needs_password_setup + setup_token）
        status, body = req("POST", "/api/v1/auth/login", {"username": "老师", "password": "x"})
        check("u_admin 首登引导补设", status == 200 and body.get("needs_password_setup") is True and "setup_token" in body)
        check("补设响应无 password_hash", "password_hash" not in body.get("user", {}))
        setup_token = body["setup_token"]

        # 3) setup-password → 正式 access_token
        status, body = req("POST", "/api/v1/auth/setup-password", {"setup_token": setup_token, "password": "admin-pass-1"})
        check("setup-password 发正式 token", status == 200 and body.get("access_token"), f"status={status}")
        admin_token = body["access_token"]

        # 4) 用正式 token 查 /users/me → head
        status, body = req("GET", "/api/v1/users/me", token=admin_token)
        check("admin me 身份 head", status == 200 and body["user"]["role"] == "head", f"{status} {body}")

        # 5) 重复 setup → 409
        status, _ = req("POST", "/api/v1/auth/setup-password", {"setup_token": setup_token, "password": "another"})
        check("重复 setup 409", status == 409, f"status={status}")

        # 6) 建带密码学生 → 直接登录成功
        status, body = req("POST", "/api/v1/admin/members", {"username": "小明", "password": "xiao-pass"}, token=admin_token)
        check("建带密码学生", status == 200 and "password_hash" not in body.get("user", {}), f"status={status}")
        student_id = body["user"]["id"]
        status, body = req("POST", "/api/v1/auth/login", {"username": "小明", "password": "xiao-pass"})
        check("带密学生直接登录", status == 200 and body.get("access_token") and "needs_password_setup" not in body, f"status={status}")
        student_token = body["access_token"]
        status, body = req("GET", "/api/v1/users/me", token=student_token)
        check("学生身份 member", status == 200 and body["user"]["role"] == "member" and body["user"]["id"] == student_id)

        # 7) 密码错误 → 401
        status, _ = req("POST", "/api/v1/auth/login", {"username": "小明", "password": "wrong"})
        check("错误密码 401", status == 401, f"status={status}")

        # 8) 建无密码学生 → 首登引导补设 → 设密后可登录
        status, body = req("POST", "/api/v1/admin/members", {"username": "小红"}, token=admin_token)
        status2, body2 = req("POST", "/api/v1/auth/login", {"username": "小红", "password": "whatever"})
        check("无密学生引导补设", status == 200 and status2 == 200 and body2.get("needs_password_setup") is True, f"{status}/{status2}")
        status3, body3 = req("POST", "/api/v1/auth/setup-password", {"setup_token": body2["setup_token"], "password": "hong-pass"})
        check("无密学生设密登录", status3 == 200 and body3.get("access_token"), f"status={status3}")

        # 9) change-password：旧密错 400 → 旧密对 ok → 新密登录
        status, _ = req("POST", "/api/v1/auth/change-password", {"old_password": "nope", "new_password": "new-pass-1"}, token=student_token)
        check("改密旧密错 400", status == 400, f"status={status}")
        status, _ = req("POST", "/api/v1/auth/change-password", {"old_password": "xiao-pass", "new_password": "new-pass-1"}, token=student_token)
        check("改密成功", status == 200, f"status={status}")
        status, _ = req("POST", "/api/v1/auth/login", {"username": "小明", "password": "xiao-pass"})
        status2, _ = req("POST", "/api/v1/auth/login", {"username": "小明", "password": "new-pass-1"})
        check("改密后旧密失效新密生效", status == 401 and status2 == 200, f"{status}/{status2}")

        # 10) 伪造 token → 401（模拟篡改/伪造）
        status, _ = req("GET", "/api/v1/users/me", token="eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1X2FkbWluIn0.forged")
        check("伪造 token 401", status == 401, f"status={status}")

        # 11) /admin/users 不泄漏 password_hash
        status, body = req("GET", "/api/v1/admin/users", token=admin_token)
        check("admin/users 列表无哈希", status == 200 and all("password_hash" not in u for u in body.get("users", [])), f"status={status}")

        # 12) scope=setup 的 token 不能当 access 用
        status, body = req("POST", "/api/v1/auth/login", {"username": "小红", "password": "whatever"})
        setup2 = body.get("setup_token", "")
        status2, _ = req("GET", "/api/v1/users/me", token=setup2)
        check("setup token 不能当 access", status2 == 401, f"status={status2}")

        # 13) 模拟 MySQL 挂掉（真实 MySQL 不可停）：合法 token → 降级身份可用，
        #     管理端点 fail-closed 503（不因 DB 挂升级权限）。
        from sqlalchemy.exc import OperationalError as _OpErr

        def _boom():
            raise _OpErr("stmt", {}, Exception("can not connect to mysql"))

        org._session_factory = _boom
        seed_module._session_factory = _boom
        status, _ = req("GET", "/api/v1/users/me", token=admin_token)
        check("DB 挂降级：个人端点可用", status == 200, f"status={status}")
        status, _ = req("GET", "/api/v1/admin/users", token=admin_token)
        check("DB 挂降级：管理端点 503", status == 503, f"status={status}")
        org._session_factory = Session
        seed_module._session_factory = Session
    finally:
        server.should_exit = True
        time.sleep(0.5)
        engine.dispose()  # 释放连接池，Windows 下才能删除 SQLite 文件

    print(f"\nPASS {len(passed)} / FAIL {len(failed)}")
    for name in passed:
        print(f"  [ok] {name}")
    for name in failed:
        print(f"  [!!] {name}")
    try:
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
    except OSError:
        pass  # Windows 偶发占用，可忽略（.work 目录，不影响运行）
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
