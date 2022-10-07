import statistics
import datetime
from dataclasses import dataclass
from typing import Dict, List

from django.http import HttpResponse
from django.shortcuts import render

from .models import ConnectBlockEvent


@dataclass
class BlockConnView:
    height: int
    events: List[ConnectBlockEvent]

    def __post_init__(self):
        if not self.events:
            return

        def fromts(ts):
            return datetime.datetime.fromtimestamp(ts)

        times = {e.host: e.timestamp.timestamp() for e in self.events}
        self.avg_got_time: datetime.datetime = fromts(statistics.mean(times.values()))
        self.stddev_got_time: float = statistics.pstdev(times.values())
        self.min: float = min(times.values())
        self.min_dt = fromts(self.min)
        self.diffs: Dict[str, float] = {host: t - self.min for host, t in times.items()}


def home(request):
    context = {'blockconnects': []}
    heights = list(
        ConnectBlockEvent.objects.values_list("height", flat=True)
        .order_by("-height")
        .distinct()[:10]
    )
    cbs = list(ConnectBlockEvent.objects.filter(height__in=heights))

    for height in heights:
        height_cbs = [cb for cb in cbs if cb.height == height]
        context['blockconnects'].append(BlockConnView(height, height_cbs))

    return render(request, "bmon/home.html", context)
