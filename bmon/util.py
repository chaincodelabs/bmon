import decimal
import json
import re
import cProfile
import time
import pstats
import huey
import http.client
import urllib
import os
import logging
from collections import Counter

from django.db import models
from django.db.models.sql.query import Query

from . import server_tasks

log = logging.getLogger(__name__)


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return str(o)
        return super(DecimalEncoder, self).default(o)


def json_dumps(*args, **kwargs):
    """Handles serialization of decimals."""
    kwargs.setdefault("cls", DecimalEncoder)
    return json.dumps(*args, **kwargs)


json_loads = json.loads


def print_sql(q: models.QuerySet | Query):
    from pygments import highlight
    from pygments.formatters import TerminalFormatter
    from pygments.lexers import PostgresLexer
    from sqlparse import format

    """Prettyprint a Django queryset."""
    if hasattr(q, "q"):
        q = q.query  # type: ignore
    formatted = format(str(q), reindent=True)
    print(highlight(formatted, PostgresLexer(), TerminalFormatter()))


def profile(cmd):
    cProfile.run(cmd, "stats")
    p = pstats.Stats("stats")
    p.sort_stats(pstats.SortKey.CUMULATIVE).print_stats(30)


def exec_tasks(n, huey_instance):
    for _ in range(n):
        t = time.time()
        task = huey_instance.dequeue()
        print("executing task %s" % task)
        assert task
        task.execute()
        print("  took %s" % (time.time() - t))


def exec_mempool_tasks(n):
    return exec_tasks(n, server_tasks.mempool_q)


def _count_tasks(q) -> Counter:
    p = re.compile(rb"bmon\.[a-zA-Z_\.]+")

    def search(msg):
        m = p.search(msg)
        assert m
        return m

    return Counter(search(msg).group().decode() for msg in q.storage.enqueued_items())


def get_task_counts():
    counts = {}
    counts.update(dict(_count_tasks(server_tasks.mempool_q)))
    counts.update(dict(_count_tasks(server_tasks.server_q)))
    return counts


def count_tasks():
    print(_count_tasks(server_tasks.mempool_q))
    print(_count_tasks(server_tasks.server_q))


def remove_mempool_events(q: huey.RedisHuey):
    clean_queue(q, "Mempool")
    clean_queue(q, "Pong")


def pushover_notification(msg: str) -> bool:
    token = os.environ.get("PUSHOVER_TOKEN")

    if not token:
        log.error("no pushover token configured")
        return False

    try:
        conn = http.client.HTTPSConnection("api.pushover.net:443")
        conn.request(
            "POST",
            "/1/messages.json",
            urllib.parse.urlencode(
                {
                    "token": token,
                    "user": os.environ.get("PUSHOVER_USER"),
                    "message": msg,
                }
            ),
            {"Content-type": "application/x-www-form-urlencoded"},
        )
        resp = conn.getresponse()
        if resp.status != 200:
            log.error("pushover request failed", extra={"response": resp, "msg": msg})
            return False
    except Exception:
        log.exception("pushover request failed")
        return False
    return True


def clean_queue(q: huey.RedisHuey, filter_str: str):
    num_exs = 0
    re = 0
    processed = 0

    while True:
        processed += 1

        if processed % 1000 == 0:
            print(processed)

        try:
            t = q.dequeue()
        except Exception:
            num_exs += 1
            continue

        if not t:
            break

        if filter_str in str(t):
            continue
        else:
            q.enqueue(t)
            re += 1

    print(f"exceptions: {num_exs}")
    print(f"requeued: {re}")
