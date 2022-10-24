# Copyright (C) 2007 Jan-Klaas Kollhof
# Copyright (C) 2011-2018 The python-bitcoinlib developers
# Copyright (C) 2020 James O'Beirne
#
# This section is part of python-bitcoinlib.
#
# It is subject to the license terms in the LICENSE file found in the top-level
# directory of the python-bitcoinlib distribution.
#
# No part of python-bitcoinlib, including this section, may be copied, modified,
# propagated, or distributed except according to the terms contained in the
# LICENSE file.
import time
import socket
import http.client as httplib
import json
import base64
import re
import urllib.parse as urlparse
import logging
from decimal import Decimal
from typing import IO


DEFAULT_USER_AGENT = "AuthServiceProxy/0.1"
DEFAULT_HTTP_TIMEOUT = 30

log = logging.getLogger("bitcoin-rpc")


class JSONRPCError(Exception):
    """JSON-RPC protocol error base class
    Subclasses of this class also exist for specific types of errors; the set
    of all subclasses is by no means complete.
    """

    def __init__(self, rpc_error):
        super(JSONRPCError, self).__init__(
            "msg: %r  code: %r" % (rpc_error["message"], rpc_error["code"])
        )
        self.error = rpc_error

    @property
    def code(self) -> int:
        return int(self.error["code"])


class BitcoinRpc(object):
    """Base JSON-RPC proxy class. Contains only private methods; do not use
    directly."""

    def __init__(
        self,
        service_url,
        service_port=None,
        net_name=None,
        timeout=DEFAULT_HTTP_TIMEOUT,
        debug_stream: IO | None = None,
        wallet_name=None,
    ):

        self.debug_stream = debug_stream
        authpair = None
        net_name = net_name or "mainnet"
        self.timeout = timeout
        self.net_name = net_name

        if service_port is None:
            service_port = {
                "mainnet": 8332,
                "testnet3": 18332,
                "regtest": 18443,
            }.get(net_name, 18332)

        url = urlparse.urlparse(service_url)
        authpair = "%s:%s" % (url.username or "", url.password or "")

        # Do our best to autodetect testnet.
        if url.port:
            self.net_name = net_name = {
                18332: "testnet3",
                18443: "regtest",
            }.get(url.port, 'mainnet')

        if authpair == ":":
            raise ValueError("need auth")

        if wallet_name:
            service_url = service_url.rstrip("/")
            service_url += f"/wallet/{wallet_name}"

        log.debug(f"Connecting to bitcoind: {service_url}")
        self.url = service_url

        # Credential redacted
        self.public_url = re.sub(r":[^/]+@", ":***@", self.url, 1)
        self._parsed_url = urlparse.urlparse(service_url)
        self.host = self._parsed_url.hostname

        log.debug(f"Initializing RPC client at {self.public_url}")
        # XXX keep for debugging, but don't ship:
        # logger.info(f"[REMOVE THIS] USING AUTHPAIR {authpair}")

        if self._parsed_url.scheme not in ("http",):
            raise ValueError("Unsupported URL scheme %r" % self._parsed_url.scheme)

        self.__id_count = 0

        self.__auth_header = None
        if authpair:
            self.__auth_header = b"Basic " + base64.b64encode(authpair.encode("utf8"))

    @property
    def port(self) -> int:
        if self._parsed_url.port is None:
            return httplib.HTTP_PORT
        else:
            return self._parsed_url.port

    def _getconn(self, timeout=None):
        return httplib.HTTPConnection(
            self._parsed_url.hostname,
            port=self.port,
            timeout=timeout,
        )

    def call(self, rpc_str: str, **kwargs) -> dict:
        """Call a method with a string."""
        [meth, *args] = rpc_str.split()
        return self._call(meth, *args, **kwargs)

    def _call(self, rpc_call_name, *args, **kwargs):
        self.__id_count += 1
        kwargs.setdefault("timeout", self.timeout)

        postdata = json.dumps(
            {
                "version": "1.1",
                "method": rpc_call_name,
                "params": args,
                "id": self.__id_count,
            }
        )

        log.debug(f"[{self.public_url}] calling %s%s", rpc_call_name, args)

        headers = {
            "Host": self._parsed_url.hostname,
            "User-Agent": DEFAULT_USER_AGENT,
            "Content-type": "application/json",
        }

        if self.__auth_header is not None:
            headers["Authorization"] = self.__auth_header

        path = self._parsed_url.path
        tries = 5
        backoff = 0.3
        conn = None
        while tries:
            try:
                conn = self._getconn(timeout=kwargs["timeout"])
                conn.request("POST", path, postdata, headers)
            except (BlockingIOError, httplib.CannotSendRequest, socket.gaierror):
                log.exception(
                    f"hit request error: {path}, {postdata}, {self._parsed_url}"
                )
                tries -= 1
                if not tries:
                    raise
                time.sleep(backoff)
                backoff *= 2
            else:
                break

        assert conn
        response = self._get_response(conn)
        err = response.get("error")
        if err is not None:
            if isinstance(err, dict):
                raise JSONRPCError(
                    {
                        "code": err.get("code", -345),
                        "message": err.get("message", "error message not specified"),
                    }
                )
            raise JSONRPCError({"code": -344, "message": str(err)})
        elif "result" not in response:
            raise JSONRPCError({"code": -343, "message": "missing JSON-RPC result"})
        else:
            return response["result"]

    def _get_response(self, conn):
        http_response = conn.getresponse()
        if http_response is None:
            raise JSONRPCError(
                {"code": -342, "message": "missing HTTP response from server"}
            )

        rdata = http_response.read().decode("utf8")
        try:
            loaded = json.loads(rdata, parse_float=Decimal)
            log.debug(f"[{self.public_url}] -> {loaded}")
            return loaded
        except Exception:
            raise JSONRPCError(
                {
                    "code": -342,
                    "message": (
                        "non-JSON HTTP response with '%i %s' from server: '%.20s%s'"
                        % (
                            http_response.status,
                            http_response.reason,
                            rdata,
                            "..." if len(rdata) > 20 else "",
                        )
                    ),
                }
            )

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            # Prevent RPC calls for non-existing python internal attribute
            # access. If someone tries to get an internal attribute
            # of RawProxy instance, and the instance does not have this
            # attribute, we do not want the bogus RPC call to happen.
            raise AttributeError

        # Create a callable to do the actual call
        def _call_wrapper(*args, **kwargs):
            return self._call(name, *args, **kwargs)

        # Make debuggers show <function bitcoin.rpc.name> rather than <function
        # bitcoin.rpc.<lambda>>
        _call_wrapper.__name__ = name
        return _call_wrapper
