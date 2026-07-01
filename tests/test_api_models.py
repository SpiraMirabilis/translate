"""Pydantic v2 request-model tests for the web API."""
from web.api.translation import JsonFixRequest
from web.api.settings_api import SettingsUpdate


class TestJsonFixRequest:
    def test_frontend_alias_json(self):
        """The frontend posts the edited JSON under the key 'json'."""
        req = JsonFixRequest(**{"action": "fix", "json": '{"ok": 1}'})
        assert req.fixed_json == '{"ok": 1}'

    def test_field_name_also_accepted(self):
        req = JsonFixRequest(action="fix", fixed_json='{"ok": 2}')
        assert req.fixed_json == '{"ok": 2}'

    def test_retry_without_json(self):
        req = JsonFixRequest(**{"action": "retry"})
        assert req.action == "retry"
        assert req.fixed_json is None


class TestSettingsUpdate:
    def test_exclude_unset(self):
        req = SettingsUpdate(site_name="X")
        dumped = req.model_dump(exclude_unset=True)
        assert dumped == {"site_name": "X"}
