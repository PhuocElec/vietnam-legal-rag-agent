import logging
from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel

from app.core.settings import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# --------- Dependencies ---------

async def validate_api_key(api_key: str = Header(None, alias="API-Key")):
    if not settings.API_KEYS:
        return

    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API Key")

    if api_key not in settings.API_KEYS:
        raise HTTPException(status_code=403, detail="Invalid API Key")

# --------- Models ---------

class ChatMessageBody(BaseModel):
    session_id: str
    message: str

# --------- Router ---------

@router.post("/chat-messages", dependencies=[Depends(validate_api_key)])
async def post_chat_messages(body: ChatMessageBody):
    try:
        return {
            "session_id": body.session_id,
            "bot_message": f"Echo: {body.message}"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error during chat message processing")
        raise HTTPException(status_code=500, detail="Internal Server Error") from e