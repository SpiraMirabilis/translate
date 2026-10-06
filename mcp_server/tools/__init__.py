"""Tool registration. Every tool is named t9_<verb>_<noun>."""


def register_all(mcp) -> None:
    from . import books, candidates, corrections, entities, footnotes, jobs, notes, prose, scan

    for module in (books, entities, notes, prose, corrections, footnotes, candidates,
                   jobs, scan):
        module.register(mcp)
