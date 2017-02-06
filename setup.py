try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup

from m3 import __version__

config = {
        'name': 'm3',
        'packages': ['m3'],
        'version': __version__,
        'author': 'Pat Pannuto',
        'author_email': 'pat.pannuto@gmail.com',
        'url': 'https://github.com/mbus/m3-python',


        'description': 'Support tools for chips from the M3 ecosystem',
        'long_description': '''
M3 Python ICE
=============

This Python library provides tools for interfacing with the ICE debug
and development board, part of the M3 ecosystem.

In addition, several high-level tools such as a programmer, message
injector, and snooper are included with this package.''',
        'classifiers': [
            "Development Status :: 3 - Alpha",
            "Environment :: Console",
            "Intended Audience :: Developers",
            "Intended Audience :: End Users/Desktop",
            "License :: OSI Approved :: MIT License",
            "Natural Language :: English",
            "Operating System :: OS Independent",
            "Programming Language :: Python",
            "Programming Language :: Python :: 2",
            "Programming Language :: Python :: 2.7",
            #"Programming Language :: Python :: 3",
            #"Programming Language :: Python :: 3.3",
            #"Programming Language :: Python :: 3.4",
            #"Programming Language :: Python :: 3.5",
            #"Programming Language :: Python :: Implementation :: CPython",
            #"Programming Language :: Python :: Implementation :: PyPy",
            "Topic :: Software Development :: Embedded Systems",
            ],

        'install_requires': [
            'future',
            'nose',
            'pyserial',
            ],
        'entry_points': {
            'console_scripts': [
                'm3_ice_simulator = m3.ice_simulator:cmd',
                'm3_ice           = m3.m3_ice:cmd',
                ],
            },
        }

setup(**config)
