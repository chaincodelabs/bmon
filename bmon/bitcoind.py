import subprocess
import json
import logging
import typing as t
from pathlib import Path


logger = logging.getLogger(__name__)


def _sh_run(*args, check_returncode=True, **kwargs) -> (bytes, bytes, int):
    logger.debug("Running command %r", args)
    p = subprocess.Popen(
        *args, **kwargs,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)

    (stdout, stderr) = p.communicate()

    if check_returncode and p.returncode != 0:
        raise RuntimeError(
            "Command '%s' failed with code %s\nstderr:\n%s\nstdout:\n%s" % (
                args[0], p.returncode, stderr, stdout))
    return (stdout, stderr, p.returncode)


class RPCClient:
    def __init__(self, datadir: Path, path_to_cli: Path):
        self.datadir = datadir
        self.path_to_cli = path_to_cli

    def call(self, cmd,
             deserialize_output=True,
             quiet=False,
             ) -> t.Optional[dict]:
        """
        Call some bitcoin RPC command and return its deserialized output.
        """
        call = _sh_run(
            "{} -datadir={} {}".format(self.path_to_cli, self.datadir, cmd),
            check_returncode=False)

        insignificant_errors = [
            "Rewinding blocks...",
            "Loading block index...",
            "Verifying blocks...",
        ]

        if call[2] != 0:
            if not any(i in call[1].decode() for i in insignificant_errors):
                logger.debug("non-zero returncode from RPC call (%s): %s",
                             self, call)
            return None

        if not deserialize_output:
            logger.debug("rpc: %r -> %r", cmd, call[0])
        else:
            logger.debug("response for %r:\n%s",
                         cmd, json.loads(call[0].decode()))

        return json.loads(call[0].decode()) if deserialize_output else None
