"""Money as a value object. See docs/domain-model.md "Money".

Four non-negotiable rules documented there:
  1. `Decimal` everywhere, never `float`.
  2. Prices are snapshotted onto the order line at placement, not looked up live.
  3. `Money` is a value object; currency travels with the amount, arithmetic is
     closed over the type, comparing two currencies raises.
  4. VAT is computed per line and rounded once at the line, then summed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

_MINOR_UNITS_PER_MAJOR = Decimal(100)
_WHOLE = Decimal(1)


class Currency(StrEnum):
    PLN = "PLN"
    EUR = "EUR"
    USD = "USD"


class CurrencyMismatchError(Exception):
    """Raised when an operation combines two `Money` values in different currencies."""


@dataclass(frozen=True, slots=True)
class VatRate:
    rate: Decimal  # e.g. Decimal("0.08")


@dataclass(frozen=True, slots=True)
class Money:
    """An amount in minor units (grosze / cents) of a given currency.

    Minor units are integers internally so arithmetic never touches binary
    floating point. See docs/domain-model.md "Money".
    """

    minor: int
    currency: Currency

    def _check_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            msg = f"Cannot combine {self.currency} with {other.currency}"
            raise CurrencyMismatchError(msg)

    def __add__(self, other: Money) -> Money:
        self._check_currency(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check_currency(other)
        return Money(self.minor - other.minor, self.currency)

    def __mul__(self, quantity: int) -> Money:
        return Money(self.minor * quantity, self.currency)

    def with_vat(self, rate: VatRate) -> Money:
        """Apply VAT and round once, at the line. See docs/domain-model.md "Money"."""
        gross = (Decimal(self.minor) * (_WHOLE + rate.rate)).quantize(
            _WHOLE, rounding=ROUND_HALF_UP
        )
        return Money(int(gross), self.currency)

    @classmethod
    def from_decimal(cls, amount: Decimal, currency: Currency) -> Money:
        minor = (amount * _MINOR_UNITS_PER_MAJOR).quantize(_WHOLE, rounding=ROUND_HALF_UP)
        return cls(int(minor), currency)

    def to_decimal(self) -> Decimal:
        return (Decimal(self.minor) / _MINOR_UNITS_PER_MAJOR).quantize(Decimal("0.01"))
