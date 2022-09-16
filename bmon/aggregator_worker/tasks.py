import celery

from .. import config

app = celery.Celery(
    'tasks',
    broker=config.REDIS_CENTRAL_URL,
)


@app.task
def receive_bitcoind_event(host, event):
    print(f"Got bitcoind {event} from {host}")
