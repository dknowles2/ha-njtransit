"""Client for NJ Transit's private GraphQL endpoint.

This subpackage must not import ``homeassistant``. It is bundled rather than
published to PyPI, and keeping it free of Home Assistant makes a later
extraction a move rather than a rewrite. ``tests/test_layering.py`` enforces
the boundary.
"""
