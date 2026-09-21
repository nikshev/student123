# impl: FR-002-14
"""
Management command to purge expired tutor data.
"""

from django.core.management.base import BaseCommand

from ai_tutor_service.conversations.retention import purge_expired


class Command(BaseCommand):
    help = "Purge expired conversations and their messages from the database."

    def handle(self, *args, **options):
        count = purge_expired()
        self.stdout.write(f"Purged {count} expired conversation(s).")