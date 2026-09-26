import uuid
from typing import List
from fastapi import APIRouter
from db import messages_repository
from app.api.schema import GetMessagesResponse, ThreadSummary

router = APIRouter(tags=["thread"])

@router.get('/get_threads',response_model=List[ThreadSummary])
async def get_threads():
    threads=await messages_repository.get_threads()
    return [ThreadSummary(**t) for t in threads]   #[{'id':xx,'title':xx}]

@router.get('/get_thread_messages',response_model=List[GetMessagesResponse])
async def get_thread_messages(id:str):
    messages=await messages_repository.get_messages(id)
    # {"id": xx,"role": xx,"content": xx,"sources": xx,"verification": xx,"created_at": xx,}
    return [GetMessagesResponse(**m) for m in messages]

@router.delete('/delete_thread')
async def delete_thread(id:str):
    result= await messages_repository.delete_thread(id)
    return result

@router.post('/rename_thread')
async def rename_thread(id:str, new_title:str):
    await messages_repository.rename_thread(id,new_title)
    return {"status": "renamed"}

