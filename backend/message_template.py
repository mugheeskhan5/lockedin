"""Edit REEL_MESSAGE to change the text sent alongside every Discord reel."""

REEL_MESSAGE = "Here's a reel for you 👀"


def custom_message():
    # No caption, OCR or generated summary is interpolated into this text.
    if not isinstance(REEL_MESSAGE, str) or not REEL_MESSAGE.strip():
        raise ValueError('REEL_MESSAGE in backend/message_template.py must not be empty')
    text = REEL_MESSAGE.strip()
    if len(text) > 500:
        raise ValueError('Keep REEL_MESSAGE at 500 characters or fewer')
    return text
