from __future__ import annotations
from decimal import Decimal
from dataclasses import dataclass


@dataclass(frozen=True)
class Quantity:
    value: Decimal

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError(f"Quantity cannot be negative: {self.value}")

    @classmethod
    def of(cls, value: float | int | str | Decimal) -> "Quantity":
        return cls(Decimal(str(value)))

    def __add__(self, other: "Quantity") -> "Quantity":
        return Quantity(self.value + other.value)

    def __sub__(self, other: "Quantity") -> "Quantity":
        return Quantity(self.value - other.value)

    def __mul__(self, factor: float | Decimal) -> "Quantity":
        return Quantity(self.value * Decimal(str(factor)))

    def __truediv__(self, divisor: float | Decimal) -> "Quantity":
        return Quantity(self.value / Decimal(str(divisor)))

    def __lt__(self, other: "Quantity") -> bool:
        return self.value < other.value

    def __le__(self, other: "Quantity") -> bool:
        return self.value <= other.value

    def __gt__(self, other: "Quantity") -> bool:
        return self.value > other.value

    def __ge__(self, other: "Quantity") -> bool:
        return self.value >= other.value

    def __float__(self) -> float:
        return float(self.value)

    def __repr__(self) -> str:
        return f"Quantity({self.value})"
