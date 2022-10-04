import os

import django
from celery import Celery
from django.conf import settings

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bmon.settings')
django.setup()

from bmon import models

app = Celery(
    'bmon-server-tasks',
    broker=settings.REDIS_SERVER_URL,
)


@app.task
def receive_bitcoind_event(event):
    modelname = event.pop('_model')
    Model = getattr(models, modelname)

    instance = Model.objects.create(**event)
    print(f"Saved {instance}")
