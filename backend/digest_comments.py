"""Compatibility wrapper: delivery now uses a hardcoded message, never OCR/LLM text."""
from .message_template import custom_message


def make_comment(row):
    # Row is intentionally not read. OCR and captions remain in reels and
    # continue to support embeddings/clustering, independently of delivery text.
    return custom_message(), 'custom'
