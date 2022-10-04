from django.http import HttpResponse

from .models import ConnectBlockEvent


def home(request):
    heights = set(
        h.height for h in
        ConnectBlockEvent.objects.all().order_by('-height')[:20]
    )
    out = ""

    for height in heights:
        out += f"# {height}\n"

        cbs = ConnectBlockEvent.objects.filter(height=height)

        for cb in cbs:
            out += f"{cb.host}: {cb.timestamp.isoformat()}\n"

        out += "\n"

    return HttpResponse(out, content_type='txt')
