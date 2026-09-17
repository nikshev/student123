"""Minimal py3.12 test-environment package for the vendored video_xblock fork.

Provides two things that exist only inside edx-platform (or pre-3.10 Python):

1. Stub top-level packages ``common``, ``openedx``, ``xmodule`` that the fork
   imports at module top-level (video_xblock/mixins.py:22-25,
   video_xblock/video_xblock.py:29).  These modules exist only inside
   edx-platform; no PyPI package provides them.  The stubs are minimal but
   behaviour-plausible (they raise NotImplementedError on the code paths that
   are only reachable inside edx-platform and are never exercised by the
   unit suite).

2. ``py312_compat``: restores ``collections.Iterable`` (and other ABC aliases)
   which Python removed in 3.10; the vendored test suite still imports it
   (video_xblock/tests/unit/test_mixins.py:6).  It also restores the
   ``unittest.TestCase`` aliases removed in Python 3.12
   (``assertRaisesRegexp`` used at video_xblock/tests/unit/test_fields.py:75).
   It is auto-loaded at Python startup via ``py312_compat.pth``; pip cannot
   place ``.pth`` files into site-packages via ``data_files``, so after
   installing this package run:

       cp py312-stubs/py312_compat.pth .venv/lib/python3.12/site-packages/

This is test-environment plumbing only: no production code of the vendored
fork is modified.  Two fork-internal code-vs-test mismatches at the vendored
HEAD (raccoongang/xblock-video @9a6f808) were aligned in the fork's test
files (test_backends.py, test_mixins.py) without touching fork code; see the
T-002 note in specs/001-bunny-video/tasks.md.  Expected unit-suite result:
154 passed, 1 skipped, 0 failed.
"""
