"""Category taxonomy (PS FR-11) with kinds (ARCH §2 A-1, A-3)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, select

from finassist.core.errors import Ambiguous, NotFound
from finassist.core.ids import new_id
from finassist.db.audit import ChangeSet, insert_many
from finassist.db.schema import category

__all__ = [
    "CategoryRow",
    "TAXONOMY",
    "UNCATEGORISED",
    "all_categories",
    "descendants",
    "resolve_category",
    "seed_taxonomy",
]

UNCATEGORISED = "Uncategorised"

# (parent, kind, is_essential, children). Kinds follow ARCH §2:
#  A-1 "Credit-card payment" is an expense only for an untracked card; a tracked card's
#      payment is classified as "Transfer between my accounts" by the transfer detector (P1).
#  A-3 "Cash withdrawal" is a transfer into the Cash account.
TAXONOMY: list[tuple[str, str, bool, list[str]]] = [
    (
        "Income",
        "income",
        False,
        [
            "Salary",
            "Freelance/Business",
            "Interest",
            "Dividends",
            "Rent received",
            "Refunds/Cashback",
            "Gifts",
            "Other income",
        ],
    ),
    (
        "Essentials",
        "expense",
        True,
        [
            "Rent/Housing",
            "Utilities",
            "Groceries",
            "Transport/Fuel",
            "Healthcare",
            "Insurance",
            "Education",
            "Childcare",
            "Domestic help",
            "Telecom/Internet",
        ],
    ),
    (
        "Lifestyle",
        "expense",
        False,
        [
            "Eating out",
            "Shopping",
            "Entertainment & Subscriptions",
            "Travel",
            "Fitness",
            "Personal care",
            "Gifts & Donations",
        ],
    ),
    ("Obligations", "expense", True, ["Loan EMI", "Credit-card payment", "Taxes", "Fees & Charges"]),
    (
        "Savings & Investments",
        "investment",
        False,
        ["SIP/Mutual funds", "Stocks", "PPF/EPF", "FD/RD", "Gold", "Crypto"],
    ),
    ("Other", "transfer", False, ["Cash withdrawal", "Transfer between my accounts", UNCATEGORISED]),
]

# Children whose kind differs from their parent's.
KIND_OVERRIDES = {UNCATEGORISED: "expense"}


@dataclass(frozen=True)
class CategoryRow:
    id: str
    parent_id: str | None
    name: str
    kind: str
    is_essential: bool
    parent_name: str | None

    @property
    def path(self) -> str:
        return f"{self.parent_name}/{self.name}" if self.parent_name else self.name


def seed_taxonomy(conn: Connection, cs: ChangeSet) -> int:
    """Insert the starting taxonomy if absent. Idempotent: returns rows inserted."""
    existing = {(r.parent_id, r.name) for r in conn.execute(select(category.c.parent_id, category.c.name))}
    parents = {
        r.name: r.id
        for r in conn.execute(select(category.c.id, category.c.name).where(category.c.parent_id.is_(None)))
    }
    rows = []
    for parent, kind, essential, children in TAXONOMY:
        pid = parents.get(parent)
        if pid is None:
            pid = new_id()
            parents[parent] = pid
            rows.append(
                {
                    "id": pid,
                    "parent_id": None,
                    "name": parent,
                    "kind": kind,
                    "is_essential": essential,
                    "is_system": True,
                }
            )
        for child in children:
            if (pid, child) in existing:
                continue
            rows.append(
                {
                    "id": new_id(),
                    "parent_id": pid,
                    "name": child,
                    "kind": KIND_OVERRIDES.get(child, kind),
                    "is_essential": essential,
                    "is_system": True,
                }
            )
    insert_many(conn, cs, category, rows, reason="seed taxonomy")
    return len(rows)


def all_categories(conn: Connection) -> list[CategoryRow]:
    parent = category.alias("parent")
    q = select(
        category.c.id,
        category.c.parent_id,
        category.c.name,
        category.c.kind,
        category.c.is_essential,
        parent.c.name.label("parent_name"),
    ).select_from(category.outerjoin(parent, category.c.parent_id == parent.c.id))
    return [
        CategoryRow(r.id, r.parent_id, r.name, r.kind, bool(r.is_essential), r.parent_name)
        for r in conn.execute(q)
    ]


def resolve_category(conn: Connection, ref: str) -> CategoryRow:
    """'Lifestyle/Eating out', 'Eating out' or 'eating out' → the category. Errors if
    unknown or if a bare name matches more than one category."""
    cats = all_categories(conn)
    key = ref.strip().lower()
    by_path = [c for c in cats if c.path.lower() == key]
    if by_path:
        return by_path[0]
    by_name = [c for c in cats if c.name.lower() == key]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        raise Ambiguous(
            f"{ref!r} matches several categories",
            hint="use Parent/Child: " + ", ".join(c.path for c in by_name),
        )
    raise NotFound(
        f"no category {ref!r}", hint="use a category name such as Groceries, Eating out or Lifestyle/Shopping"
    )


def descendants(conn: Connection, category_id: str) -> list[str]:
    """The category and all of its children (two levels)."""
    ids = [category_id]
    ids += [r.id for r in conn.execute(select(category.c.id).where(category.c.parent_id == category_id))]
    return ids
