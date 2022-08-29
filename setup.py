from setuptools import setup
import os

here = os.path.abspath(os.path.dirname(__file__))
namespace = {}

setup(
    name='bmon',
    version='0.0.1',
    description="",
    author='',
    author_email='',
    include_package_data=True,
    zip_safe=False,
    packages=['bmon'],
    install_requires=[
        'mitogen @ git+ssh://git@github.com/jamesob/mitogen.git',
        'fscm @ git+ssh://git@github.com/jamesob/fscm.git',
        'clii'
    ],
    entry_points={
        'console_scripts': [
            'bmon-parse = bmon.logparse:main',
            'bmon-mon = bmon.monitor:main',
            'bmon-server = bmon.webapp:main',
            'bmon-migrate = bmon.db:init_migrate',
        ],
    },
)
