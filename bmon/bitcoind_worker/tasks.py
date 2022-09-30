import socket

import celery

from .. import config
from ..aggregator_worker.tasks import receive_bitcoind_event


assert config.REDIS_LOCAL_URL
app = celery.Celery(
    'tasks',
    broker=config.REDIS_LOCAL_URL,
)


@app.task
def send_event(event):
    print(f"Sending event to the aggregator: {event}")
    receive_bitcoind_event.delay(socket.gethostname(), event)
