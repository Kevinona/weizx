from fastapi import APIRouter, Depends, Query

from app.api.auth import verify_token
from app.core.platform import Platform
from app.models.database import Message
from app.models.schemas import MessageOut, MessageListResponse, SendMessageRequest
from app.deps import get_message_service

router = APIRouter(prefix="/api/messages", tags=["messages"], dependencies=[Depends(verify_token)])


@router.get("", response_model=MessageListResponse)
async def list_messages(
    room_id: str = Query(""),
    user_id: str = Query(""),
    start_date: str = Query(""),
    end_date: str = Query(""),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    service=Depends(get_message_service),
):
    items, total = await service.get_messages(room_id, user_id, start_date, end_date, page, size)
    return MessageListResponse(
        items=[
            MessageOut(
                msg_id=m.msg_id,
                msg_type=m.msg_type,
                content=m.content,
                sender_wxid=m.sender_wxid,
                sender_name=m.sender_name,
                room_id=m.room_id,
                room_name=m.room_name,
                is_group=m.is_group,
                create_time=m.create_time,
            )
            for m in items
        ],
        total=total,
        page=page,
        size=size,
    )


@router.post("/send")
async def send_message(req: SendMessageRequest):
    platform = Platform.get()
    # NOTE: MacOSSender.send_text signature is
    #       send_text(msg, receiver, force_skip=False, is_group=False, target_id="")
    # Passing req.aters as the 3rd positional would map it to `force_skip` (bool),
    # which silently activates "skip search" mode whenever the aters string is
    # non-empty. Use the dedicated keyword to keep semantics correct.
    success = await platform.sender.send_text(req.msg, req.receiver, target_id=req.aters)
    return {"success": success, "msg": req.msg, "receiver": req.receiver}
