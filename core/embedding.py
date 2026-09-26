from functools import lru_cache
from core.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, EMBEDDING_MODEL
from langchain_openai import OpenAIEmbeddings


@lru_cache
def get_embedding():
    embedding_model = OpenAIEmbeddings(
        model=EMBEDDING_MODEL,
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL
    )
    return embedding_model

