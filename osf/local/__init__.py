"""Local reference implementations of the OSF contracts.

These are dependency-light, offline adapters used for the walking-skeleton eval: a temp-dir
isolation backend (real git), an in-memory forge, and a scripted agent runtime. They exist so
the end-to-end pipeline can run without API keys or network; production adapters replace them.
"""
