"""Each translation run owns its config, engine and WebInterface.

Per-request model overrides used to be written onto the one shared
TranslationConfig and undone in a `finally`. That is non-reentrant: a second
run would swap the first's model mid-chapter — and `max_chars`, hence the
chunking, is re-derived from it — then restore the value the *first* run saw
on entry. Cloning per job removes the race instead of sequencing around it.
"""
import pytest


@pytest.fixture
def make_wi(web_app):
    """The per-run WebInterface factory the start endpoints use."""
    import web.api.translation as translation_api

    return translation_api._make_web_interface


@pytest.fixture
def job():
    from web.services.job_manager import Job, JobHub

    return Job(JobHub())


def test_overrides_do_not_touch_the_process_default(make_wi, job, web_app):
    import web.api.translation as translation_api

    base = translation_api._entity_manager.config
    before = (base.translation_model, base.advice_model)

    make_wi(job, translation_model="claude:override", advice_model="oai:override")

    assert (base.translation_model, base.advice_model) == before


def test_two_runs_get_independent_configs(make_wi, job):
    a = make_wi(job, translation_model="claude:book-a")
    b = make_wi(job, translation_model="oai:book-b")

    assert a.translator.config is not b.translator.config
    assert a.translator.config.translation_model == "claude:book-a"
    assert b.translator.config.translation_model == "oai:book-b"

    # The engine reads config.translation_model repeatedly (per chunk), so the
    # isolation has to survive the other run being configured afterwards.
    assert a.translator.config.translation_model == "claude:book-a"


def test_each_run_gets_its_own_engine_and_interface(make_wi, job):
    a, b = make_wi(job), make_wi(job)

    assert a is not b
    assert a.translator is not b.translator
    # ui.py stashes one-shot per-item state on the interface (_merge_prefix,
    # _cleaned_translations); sharing one instance would leak it across runs.
    a._merge_prefix = {"title": "A"}
    assert getattr(b, "_merge_prefix", None) is None


def test_omitted_overrides_inherit_the_current_default(make_wi, job):
    import web.api.translation as translation_api

    base = translation_api._entity_manager.config
    wi = make_wi(job)

    assert wi.translator.config.translation_model == base.translation_model
    assert wi.translator.config.advice_model == base.advice_model


def test_job_is_bound_to_the_interface(make_wi, job):
    wi = make_wi(job)
    assert wi.job_manager is job
    # Callbacks must be bound to THIS job, not a process-wide singleton.
    # (Bound methods are rebuilt per attribute access, so compare __self__.)
    assert wi.should_cancel.__self__ is job
    assert wi.progress_callback.__self__ is job
