"""LLM features: product matching, natural-language rules, weekly digest.

Every feature works without an API key: each has a deterministic fallback, and
`LLMUnavailableError` is the single signal to use it.
"""
