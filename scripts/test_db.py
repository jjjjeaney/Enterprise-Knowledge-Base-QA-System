import asyncio

from db.messages_repository import create_thread, get_threads, add_message, get_messages


async def main():
   # await create_thread('hello rag')
   # return await get_threads()
     return await add_message(
        thread_id='9b2c9410-9c1a-425d-b771-e243565369e3',
        role='AI',
        content='你好',
    )
if __name__ == '__main__':
    result=asyncio.run(main())
    print(result)