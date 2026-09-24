"""Delivery-only, transactional column renames for existing SQLite databases."""
import json


def migrate_delivery(connection):
    columns = {row[1] for row in connection.execute('PRAGMA table_info(digests)')}
    if 'sent_to_telegram' in columns:
        connection.execute('ALTER TABLE digests RENAME COLUMN sent_to_telegram TO sent_to_discord')
    columns = {row[1] for row in connection.execute('PRAGMA table_info(digest_items)')}
    if 'chat_id' in columns:
        # Keep all IDs, statuses, timestamps and shared flags. Mark the old
        # transport inside the existing payload, so its IDs cannot be confused
        # with Discord IDs. Only a never-attempted item may be rebound later.
        for row in connection.execute('SELECT id,payload_json FROM digest_items').fetchall():
            try:
                payload = json.loads(row['payload_json'])
                payload['delivery_provider'] = 'telegram'
            except (ValueError, TypeError):
                # A malformed legacy payload cannot be made sendable by migration.
                continue
            connection.execute('UPDATE digest_items SET payload_json=? WHERE id=?',
                               (json.dumps(payload, ensure_ascii=False), row['id']))
        connection.execute('ALTER TABLE digest_items RENAME COLUMN chat_id TO discord_target_id')
