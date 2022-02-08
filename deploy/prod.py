#!/usr/bin/env python3
from bmon.infra import Host, BMonInstallation, MonitoredBitcoind


def main():
    rpi1 = Host("rpi4-8g-1.lan", "pi", "/data/bmon")
    rpi0 = Host("rpi4-8g-0.lan", "pi", "/data/bmon")
    fido = Host("fido.lan", "james", "/home/james/.local/bmon")
    loki_address = f"{rpi1.hostname}:3100"

    bitcoin_hosts = [rpi0, fido]

    bmon = BMonInstallation(
        rpi1, rpi1, bitcoin_hostnames=[h.hostname for h in bitcoin_hosts]
    )
    bmon.provision()

    # fido_bitcoin = MonitoredBitcoind(
    #     fido,
    #     loki_address=loki_address,
    #     version="master",
    #     rpc_user="james",
    #     rpc_password="",
    # )
    # fido_bitcoin.provision()

    rpi0_bitcoin = MonitoredBitcoind(
        rpi0,
        loki_address=loki_address,
        version="v22.0",
        rpc_user="foo",
        rpc_password="bar",
    )
    rpi0_bitcoin.provision()


if __name__ == "__main__":
    main()
