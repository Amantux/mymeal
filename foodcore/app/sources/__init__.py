"""Upstream data sources.

Each module here knows how to read ONE provider's export (``usda``, and later
``off``) and yields plain dicts in the shape ``datasets.build_dataset`` accepts.
Nothing here is imported at package import time, and nothing here reaches the
network on behalf of a lookup: a host app's request path only ever reads the
already-built dataset file.
"""
