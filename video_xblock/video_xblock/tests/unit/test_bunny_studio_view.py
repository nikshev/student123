# verifies: FR-001-01
"""Studio tab acceptance tests; preflight the package template before integration."""

from copy import deepcopy
from html import unescape
from pathlib import Path
import re
from unittest.mock import Mock, patch

from django.template import Engine, Template
from lxml import html
import pytest
import yaml

from video_xblock.backends.bunny import BunnyPlayer
from video_xblock.tests.fixtures.bunny.video_info import VIDEO_INFO_STATUSES
from video_xblock.tests.unit import base


@pytest.fixture
def block():
    """Use the fork's real XBlock harness, isolating only runtime and I/O."""
    case = base.VideoXBlockTestBase()
    case.setUp()
    xblock = case.xblock
    xblock.player_name = 'bunny'
    xblock.metadata = {'bunny_status': 'EMPTY'}
    xblock.runtime.handler_url = Mock(return_value='/handler/download_transcript')
    try:
        with patch.object(xblock, 'get_player', return_value=BunnyPlayer(xblock)), \
                patch.object(xblock, '_update_default_transcripts', return_value=([], '')), \
                patch('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden')):
            yield xblock
    finally:
        case.tearDown()


def render_studio_tab(block):
    """Observe the actual tab render, including Django's include-node path.

    Preflight only loads the specified file: it never renders or attaches it.
    The spy delegates every render unchanged and records the target's actual
    context/output. A standalone, unattached template cannot satisfy this test.
    CSS/JS resources and the production template search path are not patched.
    """
    package_dir = Path(base.__file__).resolve().parents[2]
    target = package_dir / 'templates' / 'bunny_studio_tab.html'
    engine = Engine(
        dirs=[str(target.parent)],
        debug=True,
        libraries={'video_xblock_tags': 'video_xblock.templatetags'},
    )
    engine.get_template('bunny_studio_tab.html')

    tab_renders = []
    original_render = Template._render

    def observe_render(template, context):
        rendered = original_render(template, context)
        if Path(template.origin.name).resolve() == target:
            tab_renders.append((rendered, context.flatten()))
        return rendered

    # IncludeNode calls _render rather than the outer render_template helper.
    with patch.object(Template, '_render', autospec=True, side_effect=observe_render):
        fragment = block.studio_view({})

    assert tab_renders, 'studio_view must actually render templates/bunny_studio_tab.html'
    rendered, context = tab_renders[-1]
    assert rendered.strip(), 'The attached Bunny tab must not be empty'
    assert unescape(rendered).strip() in unescape(fragment.content), (
        'The real tab output must be included in the returned Studio Fragment'
    )
    return html.fragment_fromstring(rendered, create_parent='div'), context


def visible_tree(tree):
    """Exclude non-displayed DOM content, without claiming CSS-layout coverage."""
    tree = deepcopy(tree)
    for node in list(tree.iterdescendants()):
        style = re.sub(r'\s+', '', node.get('style', '').lower())
        classes = node.get('class', '').split()
        if (node.tag in ('script', 'style', 'template')
                or 'hidden' in node.attrib or node.get('aria-hidden') == 'true'
                or 'hidden' in classes or 'is-hidden' in classes
                or 'display:none' in style or 'visibility:hidden' in style):
            if node.getparent() is not None:
                node.drop_tree()
    return tree


def test_empty_studio_tab_has_upload_button_without_manual_ids(block):
    tree, _ = render_studio_tab(block)
    visible = visible_tree(tree)
    buttons = visible.xpath('.//button | .//*[@role="button"] | .//input[@type="button" or @type="submit"]')
    assert any(
        'Завантажити' in (' '.join(button.itertext()) + button.get('value', ''))
        and 'disabled' not in button.attrib
        for button in buttons
    ), 'EMPTY must offer an enabled, visible Завантажити button'
    # href is the fork's combined URL/FileId field. Hidden transport data is OK.
    manual_id = re.compile(r'(?:^|[_-])(?:id|guid|href)(?:$|[_-])', re.I)

    def is_identifier(value):
        value = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', value)
        value = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', value)
        return manual_id.search(value)

    controls = tree.xpath('.//input[not(@type="hidden")] | .//textarea | .//select')
    assert not [
        dict(control.attrib) for control in controls
        if any(is_identifier(control.get(attr, '')) for attr in ('name', 'id'))
        and 'readonly' not in control.attrib and 'disabled' not in control.attrib
    ], 'Studio must not ask the author for video/library/account IDs or href'


@pytest.mark.parametrize('duration_divisor', [1, 2], ids=['recorded', 'derived-half'])
def test_ready_studio_tab_displays_recorded_video_duration(block, duration_divisor):
    ready = VIDEO_INFO_STATUSES[4]['body']
    # A second local metadata case derived from the recording rejects a static
    # 612.5 display; it is not represented as another recorded API response.
    seconds = ready['length'] / duration_divisor
    block.metadata = {
        'bunny_status': 'READY',
        'bunny_video_id': ready['guid'],
        'bunny_title': ready['title'],
        'bunny_length_seconds': seconds,
        'source_type': 'bunny',
        'token_protected': True,
    }
    tree, _ = render_studio_tab(block)
    text = ' '.join(visible_tree(tree).itertext())
    minutes, remainder = divmod(int(seconds), 60)
    fraction = str(seconds).split('.')[1]
    # Accept seconds (including localized decimals) or mm:ss, with optional
    # fractional seconds. Attributes and JS literals do not count as visible.
    duration = r'(?<![\d:])(?:{}|{}|{}:{:02d}(?:[.,]{})?)(?![\d:])'.format(
        re.escape(str(seconds)), re.escape(str(seconds).replace('.', ',')),
        minutes, remainder, re.escape(fraction),
    )
    assert re.search(duration, text), 'READY must display this video duration'


@pytest.mark.parametrize('use_sentinel', [False, True], ids=['yaml', 'loader-sentinel'])
def test_studio_context_exposes_upload_limits_from_yaml(block, use_sentinel):
    package_dir = Path(base.__file__).resolve().parents[2]
    with (package_dir / 'bunny_config.yaml').open(encoding='utf-8') as source:
        expected = yaml.safe_load(source)

    if use_sentinel:
        expected = deepcopy(expected)
        expected['max_upload_bytes'] //= 2
        expected['allowed_extensions'] = expected['allowed_extensions'][:1]
        # The backend currently uses this loader, not a BUNNY_CONFIG global.
        # Patch the existing source boundary, never the generated Studio context.
        with patch('video_xblock.backends.bunny.load_bunny_config', return_value=expected):
            _, context = render_studio_tab(block)
    else:
        _, context = render_studio_tab(block)

    assert 'bunny_config' in context, 'Real studio_view must supply bunny_config'
    assert context['bunny_config']['max_upload_bytes'] == expected['max_upload_bytes']
    assert context['bunny_config']['allowed_extensions'] == expected['allowed_extensions']
