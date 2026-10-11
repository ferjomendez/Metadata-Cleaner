"""Metadata Cleaner backend package.

Modules imported by the worker must only depend on the Python standard library
plus the Debian packages installed in the worker image (PIL, pikepdf). FastAPI
and argon2 are only available in the api image.
"""

__version__ = "0.1.0"
