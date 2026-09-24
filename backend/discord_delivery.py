"""Discord transport only: no gateway, privileged intents, or Instagram access."""
import asyncio
import io
import discord
from .message_template import custom_message


def client():
    return discord.Client(intents=discord.Intents.none(),
                          allowed_mentions=discord.AllowedMentions.none(),
                          max_ratelimit_timeout=30.0)


async def destination(bot, config, open_dm=False):
    target_id = config['discord_target_id']
    if config['target_kind'] == 'user':
        user = await bot.fetch_user(target_id)
        if user.bot:
            raise ValueError('Choose a human user ID for DM delivery')
        label = f'user={user} | user_id={user.id}'
        # The setup/identity command does not create a DM or send a message.
        return (await user.create_dm() if open_dm else user), label
    channel = await bot.fetch_channel(target_id)
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        raise ValueError('DISCORD_CHANNEL_ID must identify a server text channel or thread')
    return channel, f'server={channel.guild.name} | channel={channel.name} | channel_id={channel.id}'


async def send_pick(target, payload, photo, nonce):
    text = discord.utils.escape_markdown(custom_message()) + '\n' + payload['permalink']
    kwargs = {'content': text, 'silent': True,
              'allowed_mentions': discord.AllowedMentions.none(), 'nonce': nonce}
    attachment = None
    try:
        if photo:
            embed = discord.Embed(url=payload['permalink'])
            if isinstance(photo, bytes):
                attachment = discord.File(io.BytesIO(photo), filename='reel.jpg')
                kwargs['file'] = attachment
                embed.set_image(url='attachment://reel.jpg')
            else:
                embed.set_image(url=photo)
            kwargs['embed'] = embed
        # Keep network/rate-limit waits within the delivery lease. A timeout
        # after a send intent is uncertain and is never retried by the job.
        return await asyncio.wait_for(target.send(**kwargs), timeout=90)
    finally:
        if attachment:
            attachment.close()
