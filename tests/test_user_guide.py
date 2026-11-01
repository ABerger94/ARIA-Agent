"""Exercise the real offline guide, including navigation and clipboard-only prompts."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

pytest.importorskip('PyQt6')
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QApplication
from aria.user_guide import TOPICS, UserGuideWidget


@pytest.fixture
def guide():
    app = QApplication.instance() or QApplication([])
    widget = UserGuideWidget()
    widget.show()
    app.processEvents()
    yield widget
    widget.close()
    app.processEvents()


def test_searches_article_bodies_and_examples_with_clear_empty_state(guide):
    assert guide.topics.count() == len(TOPICS)
    guide.search.setText('MoDeL UTC')
    assert guide.topics.count() == 1
    assert guide.topics.currentItem().text() == 'Models and budgets'
    assert 'Daily limits use UTC' in guide.browser.toPlainText()
    guide.search.setText('three current primary')
    assert guide.topics.count() == 1
    assert guide.topics.currentItem().text() == 'Chat and background tasks'
    guide.search.setText('no-topic-has-this-string')
    assert guide.topics.count() == 0
    assert 'No topics match' in guide.summary.text()
    assert 'No topics found' in guide.browser.toPlainText()
    assert not guide.copy.isEnabled() and not guide.examples.isEnabled()
    guide.search.clear()
    assert guide.topics.count() == len(TOPICS)
    assert guide.copy.isEnabled()


def test_internal_navigation_clears_filters_and_external_links_stay_disabled(guide):
    guide.search.setText('UTC')
    guide.browser.anchorClicked.emit(QUrl('aria-guide:work-center'))
    assert guide.search.text() == ''
    assert guide.topics.currentItem().text() == 'Work Center'
    text = guide.browser.toPlainText()
    for name in ('Project settings', 'Deliverables', 'Models and resources', 'Computers'):
        assert name in text
    guide.browser.anchorClicked.emit(QUrl('https://example.invalid'))
    assert guide.topics.currentItem().text() == 'Work Center'
    assert not guide.browser.openLinks() and not guide.browser.openExternalLinks()
    assert not guide.show_topic('unknown-topic')
    assert guide.show_topic('tasks-vs-work-center')
    assert 'scheduled jobs' in guide.browser.toPlainText()
    assert 'does not replace' in guide.browser.toPlainText()


def test_copy_copies_only_selected_prompt_without_interpreting_placeholders(guide):
    assert guide.show_topic('missions')
    guide.examples.setCurrentText('Cancel a mission')
    guide.copy.click()
    assert QApplication.clipboard().text() == 'Cancel mission [mission ID].'
    assert 'Prompt copied' in guide.status.text()
    assert guide.show_topic('work-center')
    assert not guide.copy.isEnabled()
    guide.copy.click()
    assert QApplication.clipboard().text() == 'Cancel mission [mission ID].'


def test_every_bundled_cross_reference_reaches_a_topic(guide):
    import re
    keys = [topic.key for topic in TOPICS]
    assert len(keys) == len(set(keys))
    for topic in TOPICS:
        for key in re.findall(r'href="aria-guide:([^"]+)"', topic.body):
            assert guide.show_topic(key), (topic.key, key)
