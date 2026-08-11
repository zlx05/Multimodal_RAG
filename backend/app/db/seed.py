"""启动种子：确保默认管理员与默认班级存在。

Phase 1.1 真实鉴权下 u_admin 也需要账号才能登录（初始无密码 → 首登引导式补设），
所以种子必须保证 u_admin 与默认班级存在，作为班主任身份的基础。
"""

import time

from sqlalchemy.orm import Session

from ..core.database import SessionLocal
from .models import ClassGroup, ClassMember, User

DEFAULT_ADMIN_ID = "u_admin"
DEFAULT_ADMIN_NAME = "老师"
DEFAULT_CLASS_ID = "c_default"
DEFAULT_CLASS_NAME = "默认班级"

# 测试可替换为 SQLite sessionmaker（与 document_registry._session_factory 同范式）
_session_factory = SessionLocal


def seed_default_admin() -> None:
    """无 head 时创建默认班主任（u_admin），并确保默认班级及其成员关系存在。

    Phase 2C 三级权限：u_admin 是「默认班级」的班主任（role=head，高于老师 admin）。
    已存在的旧库 u_admin 是 admin，这里幂等升级为 head（数据修复，非 schema）。
    """
    with _session_factory() as db:
        if db.get(User, DEFAULT_ADMIN_ID) is None:
            db.add(
                User(
                    id=DEFAULT_ADMIN_ID,
                    username=DEFAULT_ADMIN_NAME,
                    role="head",
                    created_at=time.time(),
                )
            )
        else:
            db.query(User).filter(
                User.id == DEFAULT_ADMIN_ID, User.role != "head"
            ).update({"role": "head"})
        if db.get(ClassGroup, DEFAULT_CLASS_ID) is None:
            db.add(
                ClassGroup(
                    id=DEFAULT_CLASS_ID,
                    name=DEFAULT_CLASS_NAME,
                    admin_user_id=DEFAULT_ADMIN_ID,
                    description="默认班级",
                    created_at=time.time(),
                )
            )
        member_exists = (
            db.query(ClassMember)
            .filter(
                ClassMember.class_id == DEFAULT_CLASS_ID,
                ClassMember.user_id == DEFAULT_ADMIN_ID,
            )
            .first()
        )
        if member_exists is None:
            db.add(
                ClassMember(
                    id=f"m_{DEFAULT_ADMIN_ID}_{DEFAULT_CLASS_ID}",
                    class_id=DEFAULT_CLASS_ID,
                    user_id=DEFAULT_ADMIN_ID,
                    joined_at=time.time(),
                )
            )
        db.commit()
