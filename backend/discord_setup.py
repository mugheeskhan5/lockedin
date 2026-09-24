"""Configure a Discord bot and inspect its target identity; sends no messages."""
import argparse
import asyncio
import getpass
import json
import logging

from .db import BASE_DIR
from .discord_config import load_config, validate
from .discord_delivery import client, destination


async def identify(config):
    async with client() as bot:
        await asyncio.wait_for(bot.login(config['token']), timeout=30)
        _, label = await asyncio.wait_for(destination(bot, config), timeout=30)
        print(f'Bot: {bot.user} | bot_id={bot.user.id}')
        print(f'Destination: {label}')
        return label


async def configure():
    values = {'bot_token': getpass.getpass('Paste your Discord BOT token (hidden): ').strip()}
    kind = input('Send to a channel or user DM? Enter channel or user: ').strip().lower()
    if kind not in {'channel', 'user'}:
        raise ValueError('Choose channel or user')
    values[kind + '_id'] = input(f'Paste the {kind} ID from Discord Developer Mode: ').strip()
    values['picks_per_day'] = 4
    config = validate(values)
    await identify(config)
    if input('Is this the intended destination? Type yes to save: ').strip().lower() != 'yes':
        print('Nothing saved; no messages sent.')
        return
    temporary = BASE_DIR / 'discord.local.json.tmp'
    temporary.write_text(json.dumps(values, indent=2), encoding='utf-8')
    temporary.replace(BASE_DIR / 'discord.local.json')
    print('Saved backend/discord.local.json. No messages sent.')
    print('Environment overrides take precedence; run this command without --configure to check effective settings.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--configure', action='store_true')
    args = parser.parse_args()
    logging.getLogger('discord').setLevel(logging.WARNING)
    try:
        asyncio.run(configure() if args.configure else identify(load_config()))
        return 0
    except Exception as error:
        # Never print credentials or raw HTTP exception text.
        print(str(error) if isinstance(error, ValueError) else
              f'Discord setup failed ({type(error).__name__}); check bot token, target ID and access.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
