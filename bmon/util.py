from pygments import highlight
from pygments.formatters import TerminalFormatter
from pygments.lexers import PostgresLexer
from sqlparse import format
from django.db import models
from django.db.models.sql.query import Query
import decimal
import json


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return str(o)
        return super(DecimalEncoder, self).default(o)


def json_dumps(*args, **kwargs):
    """Handles serialization of decimals."""
    kwargs.setdefault('cls', DecimalEncoder)
    return json.dumps(*args, **kwargs)


json_loads = json.loads


def print_sql(q: models.QuerySet | Query):
    """Prettyprint a Django queryset."""
    if hasattr(q, 'q'):
        q = q.query  # type: ignore
    formatted = format(str(q), reindent=True)
    print(highlight(formatted, PostgresLexer(), TerminalFormatter()))
