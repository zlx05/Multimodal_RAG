"""会话持久化：conversations / messages / agent_traces 的读取接口。

消息的写入发生在 /chat/agent（routes_retrieval）里；这里负责创建会话、
列出会话、读取某会话的历史消息与 Agent 工具链轨迹。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .deps import get_current_user
from ..db import org
from ..db.seed import DEFAULT_CLASS_ID

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


class ConversationCreate(BaseModel):
    title: str = Field(default="", max_length=255)
    class_id: str = Field(default=DEFAULT_CLASS_ID, max_length=64)


def _owned_or_404(conversation_id: str, user_id: str) -> dict:
    conversation = org.get_conversation(conversation_id)
    if conversation is None or conversation["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conversation


@router.post("")
def create_conversation(
    body: ConversationCreate,
    current_user: dict = Depends(get_current_user),
):
    conversation = org.create_conversation(current_user["id"], body.class_id, body.title)
    return {"conversation": conversation}


@router.get("")
def list_conversations(current_user: dict = Depends(get_current_user)):
    """列出当前用户的会话，附最后一条消息预览与消息数，供前端历史列表直接展示。"""
    conversations = org.list_conversations(current_user["id"])
    for conversation in conversations:
        real = [m for m in org.list_messages(conversation["id"]) if m["role"] != "system"]
        conversation["message_count"] = len(real)
        conversation["last_message"] = (real[-1]["content"] or "")[:60] if real else ""
    return {"conversations": conversations}


@router.get("/{conversation_id}/messages")
def get_messages(conversation_id: str, current_user: dict = Depends(get_current_user)):
    _owned_or_404(conversation_id, current_user["id"])
    # 过滤掉自动压缩的滚动摘要消息（role="system"），前端只展示真实问答。
    messages = [m for m in org.list_messages(conversation_id) if m["role"] != "system"]
    return {"messages": messages}


@router.get("/{conversation_id}/traces")
def get_traces(conversation_id: str, current_user: dict = Depends(get_current_user)):
    _owned_or_404(conversation_id, current_user["id"])
    return {"traces": org.list_conversation_traces(conversation_id)}


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: str, current_user: dict = Depends(get_current_user)):
    _owned_or_404(conversation_id, current_user["id"])
    org.delete_conversation(conversation_id)
    return {"deleted": conversation_id}
