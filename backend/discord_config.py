"""Local Discord bot configuration; never commit discord.local.json."""
import json
import os
from .db import BASE_DIR


def validate(values):
    token = values.get('bot_token', '')
    if not isinstance(token, str) or not token.strip() or token.startswith('PASTE_'):
        raise ValueError('Configure DISCORD_BOT_TOKEN or run python -m backend.discord_setup --configure')
    token = token.strip()
    if any(c.isspace() for c in token):
        raise ValueError('Enter only the bot token, without a Bot prefix')
    try:
        channel = int(values.get('channel_id') or 0)
        user = int(values.get('user_id') or 0)
        count = int(values.get('picks_per_day', 4))
    except (ValueError, TypeError):
        raise ValueError('Discord IDs and picks_per_day must be integers') from None
    if channel < 0 or user < 0 or bool(channel) == bool(user):
        raise ValueError('Set exactly one of DISCORD_CHANNEL_ID or DISCORD_USER_ID (positive ID)')
    target = channel or user
    if target > 9223372036854775807:
        raise ValueError('Discord ID is outside the supported integer range')
    if not 3 <= count <= 6:
        raise ValueError('picks_per_day must be between 3 and 6')
    return {'token': token, 'discord_target_id': target,
            'target_kind': 'channel' if channel else 'user', 'count': count}


def load_config():
    path = BASE_DIR / 'discord.local.json'
    try:
        values = json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
        if not isinstance(values, dict):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise ValueError('backend/discord.local.json must contain a JSON object') from None
    values['bot_token'] = os.environ.get('DISCORD_BOT_TOKEN') or values.get('bot_token', '')
    # If either target override is present, the environment defines the entire
    # target; never silently combine a file channel with an environment user.
    if 'DISCORD_CHANNEL_ID' in os.environ or 'DISCORD_USER_ID' in os.environ:
        values['channel_id'] = os.environ.get('DISCORD_CHANNEL_ID', '')
        values['user_id'] = os.environ.get('DISCORD_USER_ID', '')
    values['picks_per_day'] = os.environ.get('DIGEST_PICKS') or values.get('picks_per_day', 4)
    return validate(values)
