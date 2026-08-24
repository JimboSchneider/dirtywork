"""ledger.py -- a small bookkeeping module for line-item invoices.

The core primitive is :func:`calc_total`, a rounding-safe sum used
throughout this module wherever a list of amounts needs to become one
number: invoice subtotals, ledger grand totals, running balances, batch
summaries, and reconciliation deltas.
"""

from __future__ import annotations


def calc_total(amounts):
    """Sum a sequence of numeric amounts, rounded to two decimal places."""
    return round(sum(amounts), 2)


class LineItem:
    """A single billable line on an invoice."""

    def __init__(self, description, quantity, unit_price):
        self.description = description
        self.quantity = quantity
        self.unit_price = unit_price

    @property
    def amount(self):
        return round(self.quantity * self.unit_price, 2)


class Invoice:
    """An invoice: a number plus a list of :class:`LineItem` objects."""

    def __init__(self, number, items=None):
        self.number = number
        self.items = list(items) if items else []

    def add_item(self, item):
        self.items.append(item)

    def subtotal(self):
        """Sum of every line item's amount on this invoice."""
        amounts = [item.amount for item in self.items]
        return calc_total(amounts)

    def tax(self, rate=0.08):
        return round(self.subtotal() * rate, 2)

    def total(self):
        return round(self.subtotal() + self.tax(), 2)


class Ledger:
    """A collection of invoices with rollup helpers."""

    def __init__(self):
        self.invoices = []

    def add_invoice(self, invoice):
        self.invoices.append(invoice)

    def subtotals(self):
        """Subtotal for every invoice in the ledger, in order."""
        return [invoice.subtotal() for invoice in self.invoices]

    def grand_subtotal(self):
        """Sum of every invoice's subtotal."""
        return calc_total(self.subtotals())

    def grand_total(self):
        """Sum of every invoice's total (subtotal + tax)."""
        totals = [invoice.total() for invoice in self.invoices]
        return calc_total(totals)


def summarize_batch(invoices):
    """Return a dict describing a batch of invoices not yet in a Ledger."""
    subtotals = [calc_total([item.amount for item in inv.items])
                 for inv in invoices]
    return {
        "count": len(invoices),
        "subtotals": subtotals,
        "batch_subtotal": calc_total(subtotals),
    }


def running_balance(starting_balance, transactions):
    """Apply a list of signed transaction amounts to a starting balance."""
    delta = calc_total(transactions)
    return round(starting_balance + delta, 2)


def average_transaction(transactions):
    """Mean of a list of signed transaction amounts, or 0.0 when empty."""
    if not transactions:
        return 0.0
    return round(calc_total(transactions) / len(transactions), 2)


def reconcile(expected_amounts, actual_amounts):
    """Difference between two amount lists, expected minus actual."""
    expected = calc_total(expected_amounts)
    actual = round(sum(actual_amounts), 2)
    return round(expected - actual, 2)


def _demo():  # pragma: no cover
    invoice = Invoice("INV-001")
    invoice.add_item(LineItem("Widget", 3, 9.99))
    invoice.add_item(LineItem("Gadget", 1, 24.5))

    ledger = Ledger()
    ledger.add_invoice(invoice)

    print("subtotal:", invoice.subtotal())
    print("total:", invoice.total())
    print("grand total:", ledger.grand_total())


if __name__ == "__main__":  # pragma: no cover
    _demo()
