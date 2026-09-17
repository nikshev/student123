"""Test-environment plumbing for running the vendored video_xblock unit
suite on Python 3.12.

Ships stub top-level packages that the fork imports from edx-platform
(``common``, ``openedx``, ``xmodule``) plus a ``py312_compat`` shim that
restores the ``collections`` ABC aliases removed in Python 3.10.
See py312-stubs/README.md.
"""
# impl: FR-001-01
from setuptools import setup, find_packages

setup(
    name='video-xblock-py312-stubs',
    version='1.0.0',
    description='edx-platform import stubs and py3.12 compat for the video_xblock unit suite',
    packages=find_packages(),
)
