from abc import ABC, abstractmethod
import math


class VectorStore(ABC):
    """Provider-agnostic similarity interface for published assistant knowledge."""

    @abstractmethod
    def search(self, query_embedding, *, language, limit):
        raise NotImplementedError


def cosine_similarity(left, right):
    if not left or not right or len(left) != len(right):
        return 0.0
    try:
        numerator = sum(float(a) * float(b) for a, b in zip(left, right))
        left_magnitude = math.sqrt(sum(float(a) ** 2 for a in left))
        right_magnitude = math.sqrt(sum(float(b) ** 2 for b in right))
    except (TypeError, ValueError):
        return 0.0
    return numerator / (left_magnitude * right_magnitude) if left_magnitude and right_magnitude else 0.0


class MySQLJsonVectorStore(VectorStore):
    """Small-volume store over JSON embeddings; only published chunks are eligible."""

    def search(self, query_embedding, *, language, limit):
        from config.ai_assistant.models import AssistantKnowledgeChunk, AssistantKnowledgeDocument

        chunks = list(AssistantKnowledgeChunk.objects.filter(
            document__status=AssistantKnowledgeDocument.STATUS_PUBLISHED,
            document__language=language,
        ).select_related('document'))
        if not chunks:
            chunks = list(AssistantKnowledgeChunk.objects.filter(
                document__status=AssistantKnowledgeDocument.STATUS_PUBLISHED,
            ).select_related('document'))
        ranked = sorted(chunks, key=lambda chunk: cosine_similarity(query_embedding, chunk.embedding), reverse=True)
        return [(chunk, cosine_similarity(query_embedding, chunk.embedding)) for chunk in ranked[:limit] if cosine_similarity(query_embedding, chunk.embedding) > 0]
