import os

import django
from django.conf import settings
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bmon.settings')
django.setup()

from bmon import server_tasks

app = Celery(
    'bmon-bitcoind-tasks',
    broker=settings.REDIS_LOCAL_URL,
)


@app.task
def send_event(event: dict):
    print(f"Sending event to the aggregator: {event}")
    server_tasks.receive_bitcoind_event.delay(event)
