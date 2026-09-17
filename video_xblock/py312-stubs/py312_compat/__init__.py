"""py3.12 compat shims for the vendored video_xblock unit suite.

Loaded automatically at interpreter startup via ``py312_compat.pth``.
"""
# impl: FR-001-01
import collections
import collections.abc as _abc

# Python 3.10 removed the deprecated aliases from `collections`;
# video_xblock/tests/unit/test_mixins.py still does
# `from collections import Iterable` (line 6).
_ABC_ALIASES = (
    'Iterable',
    'Mapping',
    'MutableMapping',
    'Sequence',
    'MutableSequence',
    'Set',
    'MutableSet',
    'Callable',
    'Container',
    'Hashable',
    'Iterator',
    'Generator',
)

for _name in _ABC_ALIASES:
    if not hasattr(collections, _name):
        setattr(collections, _name, getattr(_abc, _name))

# Python 3.12 removed the deprecated unittest aliases;
# video_xblock/tests/unit/test_fields.py still uses
# `self.assertRaisesRegexp(...)` (line 75).
import unittest as _unittest

if not hasattr(_unittest.TestCase, 'assertRaisesRegexp'):
    _unittest.TestCase.assertRaisesRegexp = _unittest.TestCase.assertRaisesRegex
if not hasattr(_unittest.TestCase, 'assertRegexpMatches'):
    _unittest.TestCase.assertRegexpMatches = _unittest.TestCase.assertRegex
if not hasattr(_unittest.TestCase, 'assertNotRegexpMatches'):
    _unittest.TestCase.assertNotRegexpMatches = _unittest.TestCase.assertNotRegex
if not hasattr(_unittest.TestCase, 'assert_'):
    _unittest.TestCase.assert_ = _unittest.TestCase.assertTrue
