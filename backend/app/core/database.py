"""SQLAlchemy 引擎与会话管理（同步）。

engine 是惰性创建的（import 时不连接数据库），首次实际查询时才建立连接，
所以 pytest 直接 import 本模块或 document_registry 不会触发 MySQL 连接。
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,   # MySQL 容器重启后自动丢弃失效连接
    pool_recycle=3600,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def _ensure_precise_timestamps() -> None:
    """把会话/消息/轨迹的 FLOAT 时间戳列升到 DOUBLE（保留亚秒精度）。

    背景：MySQL 的 FLOAT 在 epoch 量级（~1.75e9）只有 ~7 位有效数字，
    会丢掉亚秒精度、塌缩到整秒。`add_message` 的 user/assistant 两条消息
    若在同一秒写入，created_at 完全相同 → ORDER BY 平局 → 会话内消息顺序
    不稳定（实测整轮反转），既打乱历史会话回放，也让 LLM 的 chat_history
    变成"回答在前、问题在后"。仅 MySQL 需要（SQLite REAL 本身双精度）。
    幂等：只对本就还是 FLOAT 的列执行，已在 DOUBLE 的跳过。
    """
    if engine.dialect.name != "mysql":
        return
    columns = [
        ("messages", "created_at"),
        ("agent_traces", "created_at"),
        ("conversations", "created_at"),
        ("conversations", "updated_at"),
    ]
    with engine.begin() as conn:
        for table, column in columns:
            row = conn.execute(
                text(
                    "SELECT DATA_TYPE FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
                ),
                {"t": table, "c": column},
            ).fetchone()
            if row and row[0].lower() == "float":
                conn.execute(text(f"ALTER TABLE {table} MODIFY {column} DOUBLE NOT NULL"))
                print(f"[init_db] {table}.{column}: FLOAT → DOUBLE")


def _ensure_user_password_column() -> None:
    """仅 MySQL：已有 users 表补 password_hash 列（create_all 不改已存在的表）。

    Phase 1.1 真实鉴权：User 新增 password_hash 存储 bcrypt 哈希。SQLite（测试）
    的 create_all 天然含新列，只有既有 MySQL 库需要这条幂等 ALTER。
    """
    if engine.dialect.name != "mysql":
        return
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT DATA_TYPE FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'users' "
                "AND column_name = 'password_hash'"
            )
        ).fetchone()
        if row is None:
            conn.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(128) NULL"))
            print("[init_db] users.password_hash: ADD COLUMN")


def init_db() -> None:
    """建表（create_all 幂等）+ 列迁移 + 种子。在 API 与 Worker 两个进程入口各调用一次。"""
    # 确保全部 model 注册进 metadata（create_all 只建缺失表，不动已存在的表）。
    from ..db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_user_password_column()
    _ensure_precise_timestamps()

    from ..db.seed import seed_default_admin
    try:
        seed_default_admin()
    except Exception as exc:
        print(f"[init_db] 默认管理员种子失败（可忽略，仅影响身份/班级）：{exc}")


def get_db():
    """FastAPI 依赖：每次请求一个会话，结束后关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
