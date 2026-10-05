"""
Minimal Item loader, adapted from ed-donner/pricer (pricer/items.py):
https://github.com/ed-donner/pricer

Only the pieces needed to load the "ed-donner/items_full" dataset and read
its title/price/summary fields are kept here, so this project doesn't need
the upstream pricer package installed.
"""

from typing import Optional, Self

from datasets import load_dataset
from pydantic import BaseModel


class Item(BaseModel):
    """A data-point of a Product with a Price."""

    title: str
    category: str
    price: float
    full: Optional[str] = None
    weight: Optional[float] = None
    summary: Optional[str] = None
    prompt: Optional[str] = None
    id: Optional[int] = None

    def __repr__(self) -> str:
        return f"<{self.title} = ${self.price}>"

    @classmethod
    def from_hub(cls, dataset_name: str) -> tuple[list[Self], list[Self], list[Self]]:
        """Load from HuggingFace Hub and reconstruct Items."""
        ds = load_dataset(dataset_name)
        return (
            [cls.model_validate(row) for row in ds["train"]],
            [cls.model_validate(row) for row in ds["validation"]],
            [cls.model_validate(row) for row in ds["test"]],
        )
