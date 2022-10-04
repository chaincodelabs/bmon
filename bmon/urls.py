"""bmon URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from ninja import NinjaAPI

from bmon_infra.infra import get_hosts
from .views import home

api = NinjaAPI()

HOSTS = [h for h in get_hosts()[1].values() if 'bitcoin' in h.tags]


@api.get('/prom-config')
def prom_scrape_config(request):
    def get_wireguard_ip(host):
        bmon_wg = host.wireguards['wg-bmon']
        return bmon_wg.ip

    targets = [
        {
            'targets': list(filter(None, [
                f'{get_wireguard_ip(host)}:{host.bitcoind_exporter_port}',
                (
                    f'{get_wireguard_ip(host)}:{host.prom_exporter_port}' if
                    host.prom_exporter_port else ''
                ),
            ])),
            'labels': {
                'job': 'bitcoind',
                'hostname': host.name,
                'bitcoin_version': host.bitcoin_version,
                'bitcoin_dbcache': str(host.bitcoin_dbcache),
                'bitcoin_prune': str(host.bitcoin_prune),
            },
        }
        for host in HOSTS
    ]
    return targets


urlpatterns = [
    path('admin/', admin.site.urls),
    path("api/", api.urls),
    path("", home),
]
