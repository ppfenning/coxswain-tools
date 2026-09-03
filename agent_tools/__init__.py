"""Deterministic tools the agent seats call instead of spending tokens.

The mould, every time: a pure core with no I/O, the filesystem and the network
at the edges, dry-run where a write is involved, and tests that never touch a
real run. Anything a seat would otherwise do by reading a file wholesale and
reasoning about it belongs here as a function that reads it and answers.
"""

__version__ = "0.1.0"
