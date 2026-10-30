"""Yes/no settings read the way people write them in .env."""
import pytest

import config
from agent import apocalypse


@pytest.mark.parametrize('value', ['true', 'True', ' true', 'TRUE ', '1', 'yes', 'on', 'On'])
def test_yes_values(monkeypatch, value):
    monkeypatch.setenv('APEX_TEST_FLAG', value)
    assert config._flag('APEX_TEST_FLAG', False) is True


@pytest.mark.parametrize('value', ['false', '0', 'no', 'off', '', 'nope'])
def test_no_values(monkeypatch, value):
    monkeypatch.setenv('APEX_TEST_FLAG', value)
    assert config._flag('APEX_TEST_FLAG', True) is False


def test_unset_uses_default(monkeypatch):
    monkeypatch.delenv('APEX_TEST_FLAG', raising=False)
    assert config._flag('APEX_TEST_FLAG', True) is True
    assert config._flag('APEX_TEST_FLAG', False) is False


@pytest.mark.parametrize('value', ['on', ' true', 'YES'])
def test_apocalypse_flag(monkeypatch, value):
    monkeypatch.setenv('APEX_APOCALYPSE', value)
    assert apocalypse.enabled()
