"""Keeps the tests away from the real Claude Code CLI. pytest loads this file by itself, and every
test module imports it, so `python3 -m unittest discover -s tests` gets the same guard."""
import os

os.environ["EXAM_FORGE_NO_PROBE"] = "1"
