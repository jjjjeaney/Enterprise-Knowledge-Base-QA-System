from functools import lru_cache

from docling.document_converter import DocumentConverter


@lru_cache
def get_docling_converter():
    converter = DocumentConverter()
    return converter