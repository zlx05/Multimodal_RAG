"""班级学习库：用户 / 班级成员 / 上传校验审计 / 用户画像 / 长期记忆。

身份由 JWT（Phase 1.1 真实鉴权）承载，区分老师(admin)/班主任(head)/学生(member)。
老师（管理者）与学生都能上传资料；老师可在审计后台查看谁传了什么并放行/驳回/删除。
"""

import json
import shutil
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .deps import get_current_user, require_admin, require_head
from ..core import security
from ..core.config import DATA_DIR, RAG_ORIGINAL_DIR, RAG_WORK_DIR, REDIS_URL
from ..db import org
from ..db.seed import DEFAULT_ADMIN_ID, DEFAULT_CLASS_ID
from ..rag.document_registry import get_document, remove_document
from ..tasks import TaskStore, enqueue_ingestion

router = APIRouter(prefix="/api/v1", tags=["org"])
_task_store = TaskStore(REDIS_URL)
UPLOAD_DIR = DATA_DIR / "uploads"


class MemberCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    # 可选初始密码（≥6 位）；留空则学生首登走引导式补设
    password: str | None = Field(default=None, min_length=6, max_length=64)


class TeacherCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    # 可选初始密码（≥6 位）；留空则老师首登走引导式补设
    password: str | None = Field(default=None, min_length=6, max_length=64)


class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=512)


class MemberAdd(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)


class ProfileUpdate(BaseModel):
    subjects: list[str] | None = None
    weak_points: list[str] | None = None
    preferred_style: Literal["direct", "guiding", "socratic"] | None = None


class SurveySubmit(BaseModel):
    subjects: list[str] = Field(default_factory=list, max_length=30)
    weak_points: list[str] = Field(default_factory=list, max_length=30)
    answer_style: Literal["direct", "guiding", "socratic"] = "guiding"


# ---------------------------------------------------------------- 用户 / 班级

@router.post("/admin/members")
def create_member(
    body: MemberCreate,
    admin: dict = Depends(require_admin),
):
    """老师或班主任创建学生并授权进入默认班级。返回 user_id 供学生登录使用。

    Phase 2C：这里只建学生（role=member）——老师由班主任经 /admin/teachers 创建，
    堵住原先「管理员能创建管理员」的越权。Phase 1.1：可带可选初始密码，
    留空则学生首登走引导式补设。
    """
    pwd_hash = security.hash_password(body.password) if body.password else None
    user = org.create_user(body.username.strip(), role="member", password_hash=pwd_hash)
    org.add_member(DEFAULT_CLASS_ID, user["id"])
    return {"user": user, "class_id": DEFAULT_CLASS_ID}


@router.post("/admin/teachers")
def create_teacher(
    body: TeacherCreate,
    head: dict = Depends(require_head),
):
    """班主任创建老师（role=admin，同级管理员）并授权进入默认班级。可带可选初始密码。"""
    pwd_hash = security.hash_password(body.password) if body.password else None
    user = org.create_user(body.username.strip(), role="admin", password_hash=pwd_hash)
    org.add_member(DEFAULT_CLASS_ID, user["id"])
    return {"user": user, "class_id": DEFAULT_CLASS_ID}


@router.get("/admin/users")
def admin_users(admin: dict = Depends(require_admin)):
    """班级成员管理：全部用户（含角色），前端据此展示班主任/老师/学生并支持删除。"""
    return {"users": org.list_users()}


@router.delete("/admin/users/{user_id}")
def admin_delete_user(user_id: str, admin: dict = Depends(require_admin)):
    """删除用户（级联个人数据）。层级守卫：
    - 不能删自己；
    - 班主任可删任何非自己用户；
    - 老师只能删学生（member），不能动老师/班主任。
    """
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="不能删除自己的账号")
    target = org.get_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if admin["role"] == "admin" and target["role"] != "member":
        raise HTTPException(status_code=403, detail="老师只能删除学生")
    removed = org.delete_user(user_id)
    return {"deleted": removed["id"]}


@router.get("/classes")
def list_classes(current_user: dict = Depends(get_current_user)):
    return {"classes": org.list_classes()}


@router.post("/classes")
def create_class(
    body: ClassCreate,
    admin: dict = Depends(require_admin),
):
    """任何管理员可新建班级，创建者自动成为该班班主任（head）。"""
    cls = org.create_class(body.name.strip(), admin["id"], body.description)
    return {"class": cls}


@router.get("/classes/{class_id}/members")
def list_class_members(class_id: str, current_user: dict = Depends(get_current_user)):
    if org.get_class(class_id) is None:
        raise HTTPException(status_code=404, detail="班级不存在")
    return {"members": org.list_members(class_id)}


@router.post("/classes/{class_id}/members")
def add_class_member(
    class_id: str,
    body: MemberAdd,
    admin: dict = Depends(require_admin),
):
    if org.get_class(class_id) is None:
        raise HTTPException(status_code=404, detail="班级不存在")
    member = org.add_member(class_id, body.user_id)
    if member is None:
        raise HTTPException(status_code=404, detail="用户或班级不存在")
    return {"member": member}


@router.delete("/classes/{class_id}/members/{user_id}")
def remove_class_member(
    class_id: str,
    user_id: str,
    admin: dict = Depends(require_admin),
):
    removed = org.remove_member(class_id, user_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="成员不存在")
    return {"removed": removed}


def _clear_terminal_tasks(document_id: str) -> None:
    """清理同一 document_id 的旧终态任务（REJECTED/FAILED/SUCCEEDED）。

    放行重新入库时删除旧的卡死任务与旧的成功记录，避免同一份资料出现
    重复的任务卡片；任务记录是瞬态展示，原因已写入审计台账。
    """
    for key in _task_store.client.scan_iter(match="rag:task:*", count=200):
        if _task_store.client.hget(key, "document_id") != document_id:
            continue
        status = _task_store.client.hget(key, "status")
        if status in {"REJECTED", "FAILED", "SUCCEEDED"}:
            _task_store.client.delete(key)


# ---------------------------------------------------------------- 管理员审计

def _collection_has_chunks(collection_name: str) -> bool:
    """Milvus 里该 collection 是否存在且非空（判断资料是否真的已入库）。"""
    try:
        from pymilvus import Collection, connections, utility

        connections.connect(alias="default", host="127.0.0.1", port="19530")
        if not utility.has_collection(collection_name):
            return False
        return int(Collection(collection_name).num_entities) > 0
    except Exception:
        return False


def _audit_entry(upload: dict) -> dict:
    uploader = org.get_user(upload["uploader_user_id"]) or {}
    doc = get_document(upload["document_id"]) or {}
    return {
        **upload,
        "uploader": {
            "user_id": upload["uploader_user_id"],
            "username": uploader.get("username", ""),
        },
        "document": {
            "topic_label": doc.get("topic_label", ""),
            "collection_name": doc.get("collection_name", ""),
        },
        # 是否真的已入库（有非空 collection）。放行按钮据此显示：
        # approved 但未入库 = 上一次放行没生效/失败，需要再次放行补索引。
        "indexed": _collection_has_chunks(doc.get("collection_name", "")),
    }


@router.get("/admin/uploads")
def admin_uploads(
    status: str | None = None,
    admin: dict = Depends(require_admin),
):
    """审计台账：谁传了什么 + 校验结果。仅管理员可见。"""
    if status and status not in {"pending", "approved", "rejected", "hidden"}:
        raise HTTPException(status_code=400, detail=f"未知状态: {status}")
    return {"uploads": [_audit_entry(u) for u in org.list_uploads(status)]}


@router.post("/admin/uploads/{upload_id}/approve")
def approve_upload(upload_id: str, admin: dict = Depends(require_admin)):
    """管理员放行。

    只要资料未真正入库（曾被驳回、或上一次放行没生效/入库失败）就重新入队
    （skip_review 跳过校验）补索引；已入库则只更新台账，不重复入库。
    """
    upload = org.get_upload(upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="上传记录不存在")
    org.update_upload(
        upload_id,
        status="approved",
        reviewed_by="admin",
        reviewed_at=time.time(),
        review_note="管理员放行",
    )
    record = get_document(upload["document_id"])
    if record:
        collection_name = record.get("collection_name") or f"rag_{upload['document_id']}"
        if not _collection_has_chunks(collection_name):
            # 清掉旧的卡死任务（REJECTED/FAILED），避免同一资料出现两张任务卡片。
            _clear_terminal_tasks(upload["document_id"])
            task_id = _task_store.create_task(
                document_id=upload["document_id"],
                filename=record.get("filename", upload["filename"]),
                source_path=record["source_path"],
                collection_name=collection_name,
                chunk_profile=record.get("chunk_profile", "auto"),
                skip_review=True,
            )
            enqueue_ingestion(_task_store, task_id)
            return {"status": "approved", "task_id": task_id}
    return {"status": "approved"}


@router.post("/admin/uploads/{upload_id}/reject")
def reject_upload(upload_id: str, admin: dict = Depends(require_admin)):
    """管理员驳回（标记隐藏，记录与文件保留）。

    若该资料已入库，同时删除 Milvus collection——驳回即隐藏，不再出现在
    检索与切片检查里；重新放行时 approve 会自动重新入库。
    """
    upload = org.get_upload(upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="上传记录不存在")
    org.update_upload(
        upload_id,
        status="rejected",
        reviewed_by="admin",
        reviewed_at=time.time(),
        review_note="管理员驳回",
    )
    record = get_document(upload["document_id"]) or {}
    collection_name = record.get("collection_name") or f"rag_{upload['document_id']}"
    try:
        from pymilvus import connections, utility

        connections.connect(alias="default", host="127.0.0.1", port="19530")
        if utility.has_collection(collection_name):
            utility.drop_collection(collection_name)
    except Exception as exc:
        print(f"[org] 驳回时删除 collection {collection_name} 失败: {exc}")
    return {"status": "rejected"}


@router.delete("/admin/uploads/{upload_id}")
def delete_upload(upload_id: str, admin: dict = Depends(require_admin)):
    """管理员删除：移除上传记录 + 文档记录 + 文件 + Milvus collection。"""
    upload = org.get_upload(upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="上传记录不存在")
    document_id = upload["document_id"]
    record = get_document(document_id) or {}
    collection_name = record.get("collection_name", f"rag_{document_id}")

    # 删除文件与工作目录（与 routes_documents.delete_document 同逻辑）
    for path in UPLOAD_DIR.glob(f"{document_id}.*"):
        path.unlink(missing_ok=True)
    for path in Path(RAG_ORIGINAL_DIR).glob(f"{document_id}.*"):
        path.unlink(missing_ok=True)
    work_dir = Path(RAG_WORK_DIR) / document_id
    if work_dir.exists():
        try:
            shutil.rmtree(work_dir) if work_dir.is_dir() else work_dir.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        from pymilvus import connections, utility

        connections.connect(alias="default", host="127.0.0.1", port="19530")
        if utility.has_collection(collection_name):
            utility.drop_collection(collection_name)
    except Exception:
        pass

    org.delete_upload(upload_id)
    remove_document(document_id)
    return {"deleted": upload_id, "document_id": document_id}


@router.get("/admin/metrics")
def admin_metrics(admin: dict = Depends(require_admin)):
    """运行指标（Phase 2.1）：解析/入库/问答延迟、OCR 失败率、token 成本。

    Redis 不可用时返回空指标（metrics 层静默降级，不影响业务）。
    """
    from ..rag.metrics import get_metrics

    return {"metrics": get_metrics().snapshot()}


# ---------------------------------------------------------------- 首次调查报告

def _survey_view(survey: dict | None) -> dict | None:
    if survey is None:
        return None
    return {
        "user_id": survey["user_id"],
        "subjects": json.loads(survey.get("subjects") or "[]"),
        "weak_points": json.loads(survey.get("weak_points") or "[]"),
        "answer_style": survey.get("answer_style", "guiding"),
    }


@router.get("/users/me/onboarding")
def my_onboarding(current_user: dict = Depends(get_current_user)):
    """调查是否要做：仅学生（member）且没做过调查时才需要；班主任/老师永不弹。"""
    survey = org.get_survey(current_user["id"])
    needs = current_user.get("role") == "member" and survey is None
    return {"needs_onboarding": needs, "survey": _survey_view(survey)}


@router.post("/users/me/survey")
def submit_survey(
    body: SurveySubmit,
    current_user: dict = Depends(get_current_user),
):
    """学生首次调查报告：只允许一次（已调查 409）。写调查 + 初始化画像。"""
    if current_user.get("role") != "member":
        raise HTTPException(status_code=403, detail="仅学生需要填写调查报告")
    if org.get_survey(current_user["id"]) is not None:
        raise HTTPException(status_code=409, detail="调查报告已提交过")
    survey = org.create_survey(
        current_user["id"],
        subjects=body.subjects,
        weak_points=body.weak_points,
        answer_style=body.answer_style,
    )
    return {"survey": _survey_view(survey), "needs_onboarding": False}


# ---------------------------------------------------------------- 用户画像

@router.get("/users/me")
def my_identity(current_user: dict = Depends(get_current_user)):
    """当前身份（登录页校验用）：返回 id/username/role，前端据此区分管理员/学生。"""
    return {
        "user": {
            "id": current_user["id"],
            "username": current_user.get("username", ""),
            "role": current_user.get("role", "member"),
        }
    }


@router.get("/users/me/profile")
def my_profile(current_user: dict = Depends(get_current_user)):
    profile = org.get_profile(current_user["id"])
    if profile is None:
        profile = org.upsert_profile(current_user["id"])
    return {
        "user_id": current_user["id"],
        "subjects": json.loads(profile.get("subjects") or "[]"),
        "weak_points": json.loads(profile.get("weak_points") or "[]"),
        "preferred_style": profile.get("preferred_style", "standard"),
        "profile_version": profile.get("profile_version", 1),
    }


@router.put("/users/me/profile")
def update_my_profile(
    body: ProfileUpdate,
    current_user: dict = Depends(get_current_user),
):
    profile = org.upsert_profile(
        current_user["id"],
        subjects=body.subjects,
        weak_points=body.weak_points,
        preferred_style=body.preferred_style,
    )
    return {
        "user_id": current_user["id"],
        "subjects": json.loads(profile.get("subjects") or "[]"),
        "weak_points": json.loads(profile.get("weak_points") or "[]"),
        "preferred_style": profile.get("preferred_style", "standard"),
        "profile_version": profile.get("profile_version", 1),
    }


# ---------------------------------------------------------------- 长期记忆

@router.get("/users/me/memory")
def my_memory(current_user: dict = Depends(get_current_user)):
    return {"memory": org.list_memory(current_user["id"])}


@router.delete("/users/me/memory/{memory_id}")
def delete_memory(memory_id: str, current_user: dict = Depends(get_current_user)):
    removed = org.delete_memory(memory_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="记忆记录不存在")
    return {"deleted": memory_id}


# ---------------------------------------------------------------- 管理员查看学生画像/记忆

def _require_user(user_id: str):
    """校验目标用户存在（404），否则返回 None。"""
    user = org.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


def _profile_payload(profile: dict, user_id: str) -> dict:
    return {
        "user_id": user_id,
        "subjects": json.loads(profile.get("subjects") or "[]"),
        "weak_points": json.loads(profile.get("weak_points") or "[]"),
        "preferred_style": profile.get("preferred_style", "standard"),
        "profile_version": profile.get("profile_version", 1),
    }


@router.get("/admin/users/{user_id}/profile")
def admin_user_profile(user_id: str, admin: dict = Depends(require_admin)):
    """老师查看某学生的画像（只读）。空画像返回默认值，不 upsert 写库。

    与 my_profile 的自写自读不同：老师只读查看不该产生写操作。
    """
    _require_user(user_id)
    profile = org.get_profile(user_id)
    if profile is None:
        return _profile_payload(
            {"subjects": [], "weak_points": [], "preferred_style": "standard", "profile_version": 1},
            user_id,
        )
    return _profile_payload(profile, user_id)


@router.get("/admin/users/{user_id}/memory")
def admin_user_memory(user_id: str, admin: dict = Depends(require_admin)):
    """老师查看某学生的长期记忆（只读）。"""
    _require_user(user_id)
    return {"memory": org.list_memory(user_id)}
