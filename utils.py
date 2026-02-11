"""Utility helpers for crypto-address parsing."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True)
class ChainPattern:
    """Definition of one blockchain address pattern.

    Fields:
        chain: Human-readable chain code (e.g., BTC, ETH, TRON).
        regex: Compiled regex used to find candidate addresses.
    """

    chain: str
    regex: re.Pattern[str]


# Registry-based approach keeps parser extensible for new chains.
CHAIN_PATTERNS: tuple[ChainPattern, ...] = (
    # Legacy Base58 Bitcoin addresses (P2PKH + P2SH), 26-35 chars.
    ChainPattern("BTC", re.compile(r"(?<![A-Za-z0-9])[13][a-km-zA-HJ-NP-Z1-9]{25,34}(?![A-Za-z0-9])")),
    # Bech32 Bitcoin addresses.
    ChainPattern("BTC", re.compile(r"(?<![A-Za-z0-9])(bc1)[ac-hj-np-z02-9]{11,71}(?![A-Za-z0-9])", re.IGNORECASE)),
    # Ethereum addresses with 0x prefix.
    ChainPattern("ETH", re.compile(r"(?<![A-Za-z0-9])0x[a-fA-F0-9]{40}(?![A-Za-z0-9])")),
    # Tron addresses (Base58, starts with T, fixed length 34).
    ChainPattern("TRON", re.compile(r"(?<![A-Za-z0-9])T[1-9A-HJ-NP-Za-km-z]{33}(?![A-Za-z0-9])")),
)


def chain_candidates() -> Iterable[ChainPattern]:
    """Return all available chain patterns.

    Exposed as function to make extension/replacement straightforward.
    """

    return CHAIN_PATTERNS


def detect_chain(address: str) -> str:
    """Detect chain by matching address against known patterns.

    Returns:
        Chain code or ``Unknown`` if no pattern matches.
    """

    for pattern in chain_candidates():
        if pattern.regex.fullmatch(address):
            return pattern.chain
    return "Unknown"
