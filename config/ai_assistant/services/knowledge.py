import hashlib
import math
import re

from config.ai_assistant.models import AssistantKnowledgeChunk, AssistantKnowledgeDocument


def content_hash(content):
    return hashlib.sha256((content or '').encode('utf-8')).hexdigest()


def split_document(content, chunk_size=1200, overlap=180):
    text = re.sub(r'\s+', ' ', content or '').strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            sentence_end = text.rfind('. ', start, end)
            if sentence_end > start + (chunk_size // 2):
                end = sentence_end + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def rebuild_document_chunks(document):
    """Build chunks; embeddings are populated only when OpenAI is configured."""
    chunks = split_document(document.content)
    document.chunks.all().delete()
    AssistantKnowledgeChunk.objects.bulk_create([
        AssistantKnowledgeChunk(
            document=document,
            position=index,
            content=chunk,
            content_hash=content_hash(chunk),
        )
        for index, chunk in enumerate(chunks)
    ])
    return len(chunks)


def embed_document_chunks(document):
    """Populate chunks from OpenAI only when the provider is configured."""
    from config.ai_assistant.models import AssistantConfiguration
    from config.ai_assistant.services.openai_client import OpenAIClient

    client = OpenAIClient()
    if not client.configured:
        return 0
    config = AssistantConfiguration.get_solo()
    updated = 0
    for chunk in document.chunks.filter(embedding=[]):
        chunk.embedding = client.create_embedding(
            model=config.embedding_model,
            content=chunk.content,
        )
        chunk.save(update_fields=['embedding'])
        updated += 1
    return updated


def _token_overlap_score(query, content):
    query_terms = {token[:6] for token in re.findall(r'[\wáéíóúñ]{3,}', query.lower())}
    content_terms = {token[:6] for token in re.findall(r'[\wáéíóúñ]{3,}', content.lower())}
    if not query_terms:
        return 0
    return len(query_terms & content_terms) / math.sqrt(len(query_terms) * max(len(content_terms), 1))


def search_published_knowledge(query, *, language='es', limit=4):
    """Safe MVP retrieval; upgrades to cosine similarity once embeddings exist."""
    chunks = AssistantKnowledgeChunk.objects.filter(
        document__status=AssistantKnowledgeDocument.STATUS_PUBLISHED,
    ).select_related('document')
    language_chunks = [chunk for chunk in chunks if chunk.document.language == language]
    candidates = language_chunks or list(chunks)
    ranked = sorted(candidates, key=lambda chunk: _token_overlap_score(query, chunk.content), reverse=True)
    return [
        {
            'title': chunk.document.title,
            'content': chunk.content,
            'category': chunk.document.category,
            'source_url': chunk.document.source_url,
        }
        for chunk in ranked[:limit]
        if _token_overlap_score(query, chunk.content) > 0
    ]
