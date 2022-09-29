#!/usr/bin/env python3

import os
import sys
import argparse
import typing as t
from pathlib import Path
from dataclasses import dataclass

from fscm import run, p


CORE_URL = "https://bitcoincore.org/bin"
VERSIONPREFIX = "bitcoin-core-"


@dataclass
class Release:
    version: str
    url: str
    sha256: str


# Hashes were verified by me with the use of the bitcoin-core
# ./contrib/verifybinaries/verify.py script.
releases = [
    Release(
        '24.0rc1',
        f'{CORE_URL}/bitcoin-core-24.0/test.rc1/bitcoin-24.0rc1-x86_64-linux-gnu.tar.gz',
        'a05352b7feedeba71f4f124ccaa3d69a85031fab85baa45a1aae74316cd754d7',
    ),
    Release(
        '23.0',
        f'{CORE_URL}/bitcoin-core-23.0/bitcoin-23.0-x86_64-linux-gnu.tar.gz',
        '2cca490c1f2842884a3c5b0606f179f9f937177da4eadd628e3f7fd7e25d26d0',
    ),
    Release(
        '22.0',
        f'{CORE_URL}/bitcoin-core-22.0/bitcoin-22.0-x86_64-linux-gnu.tar.gz',
        '59ebd25dd82a51638b7a6bb914586201e67db67b919b2a1ff08925a7936d1b16',
    ),
    Release(
        '0.18.1',
        f'{CORE_URL}/bitcoin-core-0.18.1/bitcoin-0.18.1-x86_64-linux-gnu.tar.gz',
        '600d1db5e751fa85903e935a01a74f5cc57e1e7473c15fd3e17ed21e202cfe5a',
    ),
    Release(
        '0.10.3',
        f'{CORE_URL}/bitcoin-core-0.10.3/bitcoin-0.10.3-linux64.tar.gz',
        '586eb5576f71cd1ad2a42a26f67afc87deffc51d9f75348e2c7e96b1a401e23d',
    ),
]
version_to_release = {r.version: r for r in releases}


def download_bitcoind(release: Release, dest: t.Optional[Path] = None):
    """Download and extract bitcoin binaries into local_dir."""
    dest = dest or Path.cwd()
    os.chdir('/tmp')
    filename = release.url.split('/')[-1]

    if not Path(filename).exists():
        run(f'wget {release.url}').assert_ok()

    hash = run(f'sha256sum {filename}').assert_ok()

    if (got_hash := hash.stdout.split()[0]) != release.sha256:
        raise RuntimeError(
            f"incorrect hash found for {filename}: {got_hash} "
            f"(expected {release.sha256})")

    run(f'tar xvf {filename}').assert_ok()
    dirname = 'bitcoin-' + filename.lstrip('bitcoin-').split('-')[0]
    if not dest.exists():
        p(dest).mkdir()
    run(f'mv {dirname}/bin/* {dest}').assert_ok()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('version')
    parser.add_argument(
        '--dest', '-d', help="Directory to place the downloaded binaries into", default=None)
    args = parser.parse_args()

    if args.version not in version_to_release:
        print('Unrecognized version. Options are {", ".join(version_to_release.keys())}')
        sys.exit(1)

    dest = Path(args.dest) if args.dest else None
    download_bitcoind(version_to_release[args.version], dest)


if __name__ == "__main__":
    main()
