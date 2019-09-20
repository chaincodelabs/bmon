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
        # TODO
    ],
    entry_points={
        'console_scripts': [
            'nm-parse = bmon.logparse:main',
            'nm-mon = bmon.monitor:main',
            'nm-server = bmon.webapp:main',
            'nm-migrate = bmon.db:init_migrate',
        ],
    },
)
