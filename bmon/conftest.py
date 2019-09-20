import os
from pathlib import Path
import typing as t


def read_data_file(dirname) -> t.List[str]:
    dir_path = Path(os.path.dirname(os.path.realpath(__file__)))
    return (dir_path / 'testdata' / dirname).read_text().splitlines()
