"""SQLAlchemy 模型。

- documents：文档元数据（替代原 data/document_registry.json）。
- users / classes / class_members / uploads：Phase 2 班级学习库（轻量身份、上传校验台账）。
- user_profiles / user_memory：用户画像与长期记忆。
- conversations / messages / agent_traces：会话持久化与 Agent 轨迹。
"""

from sqlalchemy import (
    Boolean,
    Double,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base


class Document(Base):
    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    collection_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    topic_label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    chunk_profile: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    # Phase 0 恒 False：legacy 回退记录从不落盘，保留在 list_documents 函数体内。
    legacy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class User(Base):
    """轻量身份。无密码（Phase 2 不做真正鉴权），role: admin=老师 / member=学生。"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    # bcrypt 哈希（Phase 1.1 真实鉴权）。NULL = 尚未设置密码，首登走引导式补设。
    password_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


class ClassGroup(Base):
    """班级：admin_user_id 是创建者（老师）。单班级起步，class_id 预留多班级。"""

    __tablename__ = "classes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    admin_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


class ClassMember(Base):
    """授权进入班级的成员记录。"""

    __tablename__ = "class_members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    class_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    joined_at: Mapped[float] = mapped_column(Float, nullable=False)
    __table_args__ = (UniqueConstraint("class_id", "user_id", name="uq_class_member"),)


class Upload(Base):
    """上传校验台账：谁传了什么 + 校验 agent 的判定。status 是检索可见性的唯一依据。"""

    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    class_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    uploader_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    # pending（校验中）/ approved（可检索）/ rejected（驳回，隐藏但保留）/ hidden（管理员手动隐藏）
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    review_payload: Mapped[str] = mapped_column(Text, nullable=False, default="")
    review_note: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    reviewed_by: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    reviewed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    __table_args__ = (Index("ix_uploads_class_status", "class_id", "status"),)


class UserProfile(Base):
    """用户画像（1:1）。subjects / weak_points 存 JSON 数组文本，preferred_style 决定回答形式。

    preferred_style 值：direct(直接给答案) / guiding(给思路) / socratic(循循善诱)。
    旧值 beginner/standard/advanced 在存量数据中兼容（读时映射，见 agent_rag._profile_block）。
    """

    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    subjects: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    weak_points: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    preferred_style: Mapped[str] = mapped_column(String(16), nullable=False, default="standard")
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)


class UserSurvey(Base):
    """首次调查报告（1:1）。存在一行 = 已做过调查（前端不再弹）。

    记录学生第一次的初始画像答案，作为「只弹一次」的标记与初始画像审计。
    之后画像的进化由 user_profiles + user_memory 承担。
    """

    __tablename__ = "user_surveys"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    subjects: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    weak_points: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    answer_style: Mapped[str] = mapped_column(String(16), nullable=False, default="guiding")
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


class UserMemory(Base):
    """长期记忆：画像进化的审计记录。memory_type: fact / preference / error_pattern。"""

    __tablename__ = "user_memory"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    memory_type: Mapped[str] = mapped_column(String(16), nullable=False, default="fact")
    content: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_question: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


class Conversation(Base):
    """一次问答会话。class_id 预留班级维度。"""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    class_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # Double：会话/消息/轨迹按时间排序，FLOAT 在 epoch 量级会丢亚秒精度导致平局乱序。
    created_at: Mapped[float] = mapped_column(Double, nullable=False)
    updated_at: Mapped[float] = mapped_column(Double, nullable=False)


class Message(Base):
    """会话中的一条消息。metadata_json 存 sources/router/tool_calls 快照。"""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[float] = mapped_column(Double, nullable=False)


class AgentTrace(Base):
    """Agent 工具调用链，按 message 归属。"""

    __tablename__ = "agent_traces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    input: Mapped[str] = mapped_column(Text, nullable=False, default="")
    output: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[float] = mapped_column(Double, nullable=False)
