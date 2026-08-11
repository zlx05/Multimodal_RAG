"""组织与会话数据访问层（Phase 2 班级学习库）。

延续 document_registry 的范式：模块级 `_session_factory = SessionLocal`，
测试用 SQLite sessionmaker 替换即可，不依赖真实 MySQL。
所有函数同步逐调用开合会话。
"""

import json
import time
import uuid
from typing import Any

from ..core.database import SessionLocal
from .models import (
    AgentTrace,
    ClassGroup,
    ClassMember,
    Conversation,
    Message,
    Upload,
    User,
    UserMemory,
    UserProfile,
    UserSurvey,
)

_session_factory = SessionLocal

DEFAULT_CLASS_ID = "c_default"


def _row_dict(row: Any) -> dict:
    return {col.name: getattr(row, col.name) for col in row.__table__.columns}


def _public_user(row: Any) -> dict:
    """返回给 API 的用户 dict（绝不携带 password_hash）。"""
    data = _row_dict(row)
    data.pop("password_hash", None)
    return data


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:28]}"


# ---------------------------------------------------------------- users

def get_user(user_id: str) -> dict | None:
    with _session_factory() as db:
        row = db.get(User, user_id)
        return _public_user(row) if row else None


def list_users() -> list[dict]:
    with _session_factory() as db:
        return [_public_user(row) for row in db.query(User).order_by(User.created_at).all()]


def create_user(username: str, role: str = "member", password_hash: str | None = None) -> dict:
    user_id = _new_id("u")
    row = User(
        id=user_id,
        username=username,
        role=role,
        password_hash=password_hash,
        created_at=time.time(),
    )
    with _session_factory() as db:
        db.add(row)
        db.commit()
        return _public_user(row)


def get_user_by_username(username: str) -> dict | None:
    """公开 dict（不含 password_hash），供 login 后展示身份。"""
    with _session_factory() as db:
        row = db.query(User).filter(User.username == username).first()
        return _public_user(row) if row else None


def get_credentials_by_username(username: str) -> dict | None:
    """auth 专用：返回完整行（含 password_hash），绝不可直接回给 API。"""
    with _session_factory() as db:
        row = db.query(User).filter(User.username == username).first()
        return _row_dict(row) if row else None


def get_credentials(user_id: str) -> dict | None:
    """auth 专用：按 id 返回完整行（含 password_hash），绝不可直接回给 API。"""
    with _session_factory() as db:
        row = db.get(User, user_id)
        return _row_dict(row) if row else None


def set_password(user_id: str, password_hash: str) -> None:
    with _session_factory() as db:
        row = db.get(User, user_id)
        if row is not None:
            row.password_hash = password_hash
            db.commit()


# ---------------------------------------------------------------- classes

def get_class(class_id: str) -> dict | None:
    with _session_factory() as db:
        row = db.get(ClassGroup, class_id)
        return _row_dict(row) if row else None


def list_classes() -> list[dict]:
    with _session_factory() as db:
        return [_row_dict(row) for row in db.query(ClassGroup).order_by(ClassGroup.created_at).all()]


def create_class(name: str, admin_user_id: str, description: str = "") -> dict:
    class_id = _new_id("c")
    row = ClassGroup(
        id=class_id, name=name, admin_user_id=admin_user_id,
        description=description, created_at=time.time(),
    )
    with _session_factory() as db:
        db.add(row)
        db.add(ClassMember(id=f"m_{admin_user_id}_{class_id}", class_id=class_id,
                           user_id=admin_user_id, joined_at=time.time()))
        db.commit()
        return _row_dict(row)


def add_member(class_id: str, user_id: str) -> dict | None:
    with _session_factory() as db:
        if db.get(ClassGroup, class_id) is None or db.get(User, user_id) is None:
            return None
        exists = db.query(ClassMember).filter(
            ClassMember.class_id == class_id, ClassMember.user_id == user_id
        ).first()
        if exists:
            return _row_dict(exists)
        row = ClassMember(id=_new_id("m"), class_id=class_id,
                          user_id=user_id, joined_at=time.time())
        db.add(row)
        db.commit()
        return _row_dict(row)


def list_members(class_id: str) -> list[dict]:
    with _session_factory() as db:
        rows = (
            db.query(ClassMember)
            .filter(ClassMember.class_id == class_id)
            .order_by(ClassMember.joined_at)
            .all()
        )
        return [_row_dict(row) for row in rows]


def remove_member(class_id: str, user_id: str) -> dict | None:
    with _session_factory() as db:
        row = db.query(ClassMember).filter(
            ClassMember.class_id == class_id, ClassMember.user_id == user_id
        ).first()
        if row is None:
            return None
        data = _row_dict(row)
        db.delete(row)
        db.commit()
        return data


# ---------------------------------------------------------------- uploads

def create_upload(document_id: str, uploader_user_id: str, filename: str,
                  source_type: str, class_id: str = DEFAULT_CLASS_ID) -> dict:
    row = Upload(
        id=_new_id("up"), document_id=document_id, class_id=class_id,
        uploader_user_id=uploader_user_id, filename=filename, source_type=source_type,
        status="pending", created_at=time.time(),
    )
    with _session_factory() as db:
        db.add(row)
        db.commit()
        return _row_dict(row)


def get_upload_by_document(document_id: str) -> dict | None:
    with _session_factory() as db:
        row = db.query(Upload).filter(Upload.document_id == document_id).first()
        return _row_dict(row) if row else None


def get_upload(upload_id: str) -> dict | None:
    with _session_factory() as db:
        row = db.get(Upload, upload_id)
        return _row_dict(row) if row else None


def list_uploads(status: str | None = None) -> list[dict]:
    with _session_factory() as db:
        q = db.query(Upload)
        if status:
            q = q.filter(Upload.status == status)
        return [_row_dict(row) for row in q.order_by(Upload.created_at.desc()).all()]


def update_upload(upload_id: str, **updates) -> dict | None:
    allowed = {"status", "review_payload", "review_note", "reviewed_by", "reviewed_at"}
    with _session_factory() as db:
        row = db.get(Upload, upload_id)
        if row is None:
            return None
        for key, value in updates.items():
            if key in allowed and hasattr(row, key):
                setattr(row, key, value)
        db.commit()
        return _row_dict(row)


def _status_document_ids(visible_statuses: tuple[str, ...]) -> set[str]:
    """有 upload 记录且 status 属于 visible_statuses 的 document_id 集合。"""
    with _session_factory() as db:
        rows = db.query(Upload.document_id).filter(Upload.status.in_(visible_statuses)).all()
        return {row[0] for row in rows}


def hidden_document_ids() -> set[str]:
    """有 upload 记录但不是 approved 的 document_id（pending/rejected/hidden 都不可见）。"""
    approved = _status_document_ids(("approved",))
    with _session_factory() as db:
        all_uploads = {row[0] for row in db.query(Upload.document_id).all()}
    return all_uploads - approved


def delete_upload(upload_id: str) -> dict | None:
    with _session_factory() as db:
        row = db.get(Upload, upload_id)
        if row is None:
            return None
        data = _row_dict(row)
        db.delete(row)
        db.commit()
        return data


# ---------------------------------------------------------------- profiles

def get_profile(user_id: str) -> dict | None:
    with _session_factory() as db:
        row = db.get(UserProfile, user_id)
        return _row_dict(row) if row else None


def upsert_profile(user_id: str, subjects: list[str] | None = None,
                   weak_points: list[str] | None = None,
                   preferred_style: str | None = None,
                   bump_version: bool = True) -> dict:
    with _session_factory() as db:
        row = db.get(UserProfile, user_id)
        if row is None:
            row = UserProfile(
                user_id=user_id,
                subjects=json.dumps(subjects or [], ensure_ascii=False),
                weak_points=json.dumps(weak_points or [], ensure_ascii=False),
                preferred_style=preferred_style or "standard",
                profile_version=1,
                updated_at=time.time(),
            )
            db.add(row)
        else:
            if subjects is not None:
                row.subjects = json.dumps(subjects, ensure_ascii=False)
            if weak_points is not None:
                row.weak_points = json.dumps(weak_points, ensure_ascii=False)
            if preferred_style is not None:
                row.preferred_style = preferred_style
            if bump_version:
                row.profile_version += 1
            row.updated_at = time.time()
        db.commit()
        return _row_dict(row)


def merge_profile_weak_points(user_id: str, new_points: list[str]) -> dict:
    """把新发现的薄弱点去重合并进画像并返回新画像。"""
    profile = upsert_profile(user_id, bump_version=False)
    if not profile:
        profile = upsert_profile(user_id)
    current = json.loads(profile["weak_points"] or "[]")
    merged = list(current)
    for point in new_points:
        if point and point not in merged:
            merged.append(point)
    return upsert_profile(user_id, weak_points=merged, bump_version=True)


# ---------------------------------------------------------------- surveys

def get_survey(user_id: str) -> dict | None:
    with _session_factory() as db:
        row = db.get(UserSurvey, user_id)
        return _row_dict(row) if row else None


def create_survey(user_id: str, subjects: list[str],
                  weak_points: list[str], answer_style: str) -> dict:
    """写首次调查报告，并把初始画像同步到 user_profiles（同一次提交原子生效）。"""
    with _session_factory() as db:
        row = db.get(UserSurvey, user_id)
        if row is not None:
            return _row_dict(row)
        row = UserSurvey(
            user_id=user_id,
            subjects=json.dumps(subjects or [], ensure_ascii=False),
            weak_points=json.dumps(weak_points or [], ensure_ascii=False),
            answer_style=answer_style,
            created_at=time.time(),
        )
        db.add(row)
        db.commit()
        data = _row_dict(row)
    upsert_profile(
        user_id,
        subjects=subjects or [],
        weak_points=weak_points or [],
        preferred_style=answer_style or "guiding",
    )
    return data


# ---------------------------------------------------------------- memory

def add_memory(user_id: str, memory_type: str, content: str,
               source_question: str = "", confidence: float = 1.0) -> dict:
    row = UserMemory(
        id=_new_id("mem"), user_id=user_id, memory_type=memory_type,
        content=content, source_question=source_question,
        confidence=confidence, created_at=time.time(),
    )
    with _session_factory() as db:
        db.add(row)
        db.commit()
        return _row_dict(row)


def list_memory(user_id: str) -> list[dict]:
    with _session_factory() as db:
        rows = (
            db.query(UserMemory)
            .filter(UserMemory.user_id == user_id)
            .order_by(UserMemory.created_at.desc())
            .all()
        )
        return [_row_dict(row) for row in rows]


def delete_memory(memory_id: str) -> dict | None:
    with _session_factory() as db:
        row = db.get(UserMemory, memory_id)
        if row is None:
            return None
        data = _row_dict(row)
        db.delete(row)
        db.commit()
        return data


# ---------------------------------------------------------------- conversations

def create_conversation(user_id: str, class_id: str = "", title: str = "") -> dict:
    row = Conversation(
        id=_new_id("conv"), user_id=user_id, class_id=class_id,
        title=title, created_at=time.time(), updated_at=time.time(),
    )
    with _session_factory() as db:
        db.add(row)
        db.commit()
        return _row_dict(row)


def get_conversation(conversation_id: str) -> dict | None:
    with _session_factory() as db:
        row = db.get(Conversation, conversation_id)
        return _row_dict(row) if row else None


def list_conversations(user_id: str) -> list[dict]:
    with _session_factory() as db:
        rows = (
            db.query(Conversation)
            .filter(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .all()
        )
        return [_row_dict(row) for row in rows]


def delete_conversation(conversation_id: str) -> dict | None:
    with _session_factory() as db:
        row = db.get(Conversation, conversation_id)
        if row is None:
            return None
        data = _row_dict(row)
        # 级联删除消息与轨迹
        for message in db.query(Message).filter(Message.conversation_id == conversation_id).all():
            db.query(AgentTrace).filter(AgentTrace.message_id == message.id).delete()
            db.delete(message)
        db.delete(row)
        db.commit()
        return data


def delete_user(user_id: str) -> dict | None:
    """删除用户及其个人数据（画像/调查/记忆/成员关系/会话）。

    上传记录**保留**：已审核上传属于共享班级资料库，删除账号不清库。
    """
    with _session_factory() as db:
        row = db.get(User, user_id)
        if row is None:
            return None
        data = _row_dict(row)
        db.query(UserProfile).filter(UserProfile.user_id == user_id).delete()
        db.query(UserSurvey).filter(UserSurvey.user_id == user_id).delete()
        db.query(UserMemory).filter(UserMemory.user_id == user_id).delete()
        db.query(ClassMember).filter(ClassMember.user_id == user_id).delete()
        for conv in db.query(Conversation).filter(Conversation.user_id == user_id).all():
            for message in db.query(Message).filter(Message.conversation_id == conv.id).all():
                db.query(AgentTrace).filter(AgentTrace.message_id == message.id).delete()
                db.delete(message)
            db.delete(conv)
        db.delete(row)
        db.commit()
        return data


# ---------------------------------------------------------------- messages / traces

def add_message(conversation_id: str, role: str, content: str,
                model: str = "", metadata_json: str = "") -> dict:
    row = Message(
        id=_new_id("msg"), conversation_id=conversation_id, role=role,
        content=content, model=model, metadata_json=metadata_json,
        created_at=time.time(),
    )
    with _session_factory() as db:
        db.add(row)
        db.query(Conversation).filter(Conversation.id == conversation_id).update(
            {"updated_at": time.time()}
        )
        db.commit()
        return _row_dict(row)


def _message_order():
    """会话消息确定性排序：created_at 升序 + 同秒次级键（user 在前）。

    Phase 2 遗留：messages.created_at 曾经是 MySQL FLOAT，epoch 量级丢精度塌到
    整秒，user/assistant 两条消息同秒 → ORDER BY 平局 → 顺序任意（实测"回答在前、
    问题在后"，既打乱历史回放也喂错 LLM 的 chat_history）。列已升 DOUBLE（新数据
    带亚秒、不再平局），旧数据整秒并列仍存在，这里补 role 次级键兜底。
    """
    from sqlalchemy import case

    return (Message.created_at, case((Message.role == "user", 0), (Message.role == "assistant", 1), else_=2))


def list_messages(conversation_id: str) -> list[dict]:
    with _session_factory() as db:
        rows = (
            db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(*_message_order())
            .all()
        )
        return [_row_dict(row) for row in rows]


def get_conversation_summary(conversation_id: str) -> str | None:
    """返回该会话最新的滚动摘要（role="system" 且 metadata 标记 summary），无则 None。"""
    with _session_factory() as db:
        row = (
            db.query(Message)
            .filter(
                Message.conversation_id == conversation_id,
                Message.role == "system",
                Message.metadata_json.contains('"summary"'),
            )
            .order_by(Message.created_at.desc())
            .first()
        )
        return row.content if row else None


def delete_messages(conversation_id: str, message_ids: list[str]) -> None:
    """批量删除消息（连同其 Agent 轨迹），用于压缩时折叠旧消息。"""
    if not message_ids:
        return
    with _session_factory() as db:
        rows = (
            db.query(Message)
            .filter(Message.conversation_id == conversation_id, Message.id.in_(message_ids))
            .all()
        )
        for message in rows:
            db.query(AgentTrace).filter(AgentTrace.message_id == message.id).delete()
            db.delete(message)
        db.commit()


def add_trace(message_id: str, step_index: int, tool: str,
              input_json: str, output: str) -> dict:
    row = AgentTrace(
        id=_new_id("tr"), message_id=message_id, step_index=step_index,
        tool=tool, input=input_json, output=output, created_at=time.time(),
    )
    with _session_factory() as db:
        db.add(row)
        db.commit()
        return _row_dict(row)


def list_traces(message_id: str) -> list[dict]:
    with _session_factory() as db:
        rows = (
            db.query(AgentTrace)
            .filter(AgentTrace.message_id == message_id)
            .order_by(AgentTrace.step_index)
            .all()
        )
        return [_row_dict(row) for row in rows]


def list_conversation_traces(conversation_id: str) -> list[dict]:
    """按消息顺序返回该会话的全部 Agent 轨迹（含消息归属）。"""
    result: list[dict] = []
    with _session_factory() as db:
        messages = (
            db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(*_message_order())
            .all()
        )
        for message in messages:
            traces = (
                db.query(AgentTrace)
                .filter(AgentTrace.message_id == message.id)
                .order_by(AgentTrace.step_index)
                .all()
            )
            for trace in traces:
                item = _row_dict(trace)
                item["message_role"] = message.role
                result.append(item)
    return result
