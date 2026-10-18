"""Measure Python subprocesses of the test suites (`[run] patch = subprocess`, see .coveragerc).

coverage.py starts a child through a .pth file it writes into site-packages; on an interpreter
whose site-packages is read-only (macOS system python3) that file is never written. Putting this
directory on PYTHONPATH starts the child the same way. Nothing happens outside a coverage run.
"""
import coverage

coverage.process_startup()
