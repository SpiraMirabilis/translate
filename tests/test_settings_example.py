"""settings.json.example must list every setting at its current default, with
the code's comments — it is generated from settings_store.SCHEMA and goes stale
whenever a setting is added or a default or comment changes."""
import settings_store


def test_example_matches_schema():
    with open(settings_store.EXAMPLE_PATH, encoding="utf-8") as f:
        on_disk = f.read()
    assert on_disk == settings_store.render_example(), (
        "settings.json.example is out of date — run: python3 settings_store.py --write-example")


def test_example_loads_as_defaults(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text(open(settings_store.EXAMPLE_PATH, encoding="utf-8").read(), encoding="utf-8")
    monkeypatch.setattr(settings_store, "_PATH", str(path))
    monkeypatch.setattr(settings_store, "_data", None)
    monkeypatch.setattr(settings_store, "_mtime_ns", None)
    assert settings_store.all_settings() == {k: d for k, (_, d, _) in settings_store.SCHEMA.items()}


def test_comments_tolerated_but_urls_kept(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text('// a comment\n{\n  // why\n  "site_base_url": "https://reader.example//x"\n}\n',
                    encoding="utf-8")
    monkeypatch.setattr(settings_store, "_PATH", str(path))
    monkeypatch.setattr(settings_store, "_data", None)
    monkeypatch.setattr(settings_store, "_mtime_ns", None)
    assert settings_store.get("site_base_url") == "https://reader.example//x"


def test_every_documented_key_is_real():
    assert set(settings_store._schema_comments()) <= set(settings_store.SCHEMA)
