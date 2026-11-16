"""Pytest configuration: make the app module importable during tests.

app.py calls genai.configure() at import time and exits if no API key is
present, so we set a dummy key before import. No network call is made by
configure(), so this is safe and keeps the tests fully offline.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test-key-not-used")
