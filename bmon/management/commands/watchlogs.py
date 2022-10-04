from django.core.management.base import BaseCommand
from django.conf import settings

from bmon.logparse import watch_logs

import logging

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Monitor bitcoind logs and trigger event handling'

    def add_arguments(self, parser):
        parser.add_argument(
            '-f', '--bitcoind-log-path', help='Path to the bitcoind log file',
            default=settings.BITCOIND_LOG_PATH)

    def handle(self, *_, **options):
        filename = options['bitcoind_log_path']
        assert filename
        watch_logs(filename)
