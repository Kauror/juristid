"""Read models for the Statistika workspace, one module per domain.

Import the modules rather than their contents: ``selectors.matters`` and
``selectors.submissions`` both define a function about Matters, and the module
prefix at the call site is what keeps it obvious which population is being
asked about.
"""
