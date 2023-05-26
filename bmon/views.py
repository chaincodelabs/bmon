from django.shortcuts import render

from bmon.models import HeaderToTipEvent


def main(request):
    return render(request, 'index.html', {})


def headertotip(request):
    events = HeaderToTipEvent.objects.filter(header_to_tip_secs__gte=5).order_by('-height')[:100]
    return render(request, 'tips.html', {"events": events})
