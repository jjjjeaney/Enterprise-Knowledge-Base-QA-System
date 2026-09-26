from fastapi import APIRouter

from db.document_repository import delete_document_by_id, list_documents
from db.vector_repository import remove_doc_chunk

router=APIRouter(tags=["document"])
@router.delete('/delete_document')
async def delete_document(id:str):
    result=await delete_document_by_id(id)
    result1=await remove_doc_chunk(id)
    return [result,result1]
@router.get('/get_documents')
async def get_documents():
    result=await list_documents()
    return result
