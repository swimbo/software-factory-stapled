"""Disposable development fixture; no network or installed packages required."""
VERSION = '0.1.0'


def normalize(name):
    return name.strip()  # Deliberately does not lowercase.


def total(prices):
    return sum(prices) + 1  # Deliberate off-by-one.
