from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    value: Decimal

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError(f"Price cannot be negative: {self.value}")

    @classmethod
    def of(cls, value: float | int | str | Decimal) -> "Price":
        return cls(Decimal(str(value)).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP))

    def __mul__(self, other: "Price | Decimal | float") -> "Price":
        if isinstance(other, Price):
            return Price(self.value * other.value)
        return Price(self.value * Decimal(str(other)))

    def __add__(self, other: "Price") -> "Price":
        return Price(self.value + other.value)

    def __sub__(self, other: "Price") -> "Price":
        return Price(self.value - other.value)

    def __truediv__(self, other: "Price | Decimal | float") -> "Price":
        if isinstance(other, Price):
            return Price(self.value / other.value)
        return Price(self.value / Decimal(str(other)))

    def __lt__(self, other: "Price") -> bool:
        return self.value < other.value

    def __le__(self, other: "Price") -> bool:
        return self.value <= other.value

    def __gt__(self, other: "Price") -> bool:
        return self.value > other.value

    def __ge__(self, other: "Price") -> bool:
        return self.value >= other.value

    def __float__(self) -> float:
        return float(self.value)

    def __repr__(self) -> str:
        return f"Price({self.value})"
