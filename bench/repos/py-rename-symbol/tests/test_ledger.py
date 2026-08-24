"""Tests for ledger.py -- py-rename-symbol.

Imports `compute_total` from ledger.py, resolved from the CURRENT WORKING
DIRECTORY (the repo root), matching the other bench fixtures' convention: the
same file must work whether it's run from the fixture directory on the host
or from /work inside the acceptance container. `compute_total` does not
exist yet, so every test below fails until the rename lands. This file is
already written for the post-rename name and never needs editing itself.
Stdlib `unittest` only; no pytest dependency.
"""
import os
import sys
import unittest

sys.path.insert(0, os.getcwd())

import ledger  # noqa: E402
from ledger import Invoice, LineItem, Ledger, compute_total  # noqa: E402


class ComputeTotalTests(unittest.TestCase):
    def test_compute_total_basic(self):
        self.assertEqual(compute_total([1, 2, 3]), 6)

    def test_compute_total_rounds(self):
        self.assertEqual(compute_total([0.111, 0.222]), 0.33)

    def test_compute_total_empty(self):
        self.assertEqual(compute_total([]), 0)


class InvoiceTests(unittest.TestCase):
    def test_invoice_subtotal_uses_compute_total(self):
        invoice = Invoice("INV-100")
        invoice.add_item(LineItem("Widget", 3, 9.99))
        invoice.add_item(LineItem("Gadget", 1, 24.5))
        self.assertEqual(invoice.subtotal(), compute_total([29.97, 24.5]))

    def test_invoice_total_includes_tax(self):
        invoice = Invoice("INV-101")
        invoice.add_item(LineItem("Widget", 1, 100.0))
        self.assertEqual(invoice.total(), 108.0)


class LedgerTests(unittest.TestCase):
    def test_grand_total(self):
        ledger_obj = Ledger()
        invoice = Invoice("INV-200")
        invoice.add_item(LineItem("Widget", 2, 10.0))
        ledger_obj.add_invoice(invoice)
        self.assertEqual(ledger_obj.grand_subtotal(), 20.0)
        self.assertEqual(ledger_obj.grand_total(), 21.6)


class BatchAndReconcileTests(unittest.TestCase):
    def test_summarize_batch(self):
        invoice = Invoice("INV-300")
        invoice.add_item(LineItem("Widget", 1, 5.0))
        summary = ledger.summarize_batch([invoice])
        self.assertEqual(summary["batch_subtotal"], 5.0)

    def test_running_balance(self):
        self.assertEqual(ledger.running_balance(100.0, [-10.0, 5.0]), 95.0)

    def test_average_transaction(self):
        self.assertEqual(ledger.average_transaction([10.0, 20.0]), 15.0)

    def test_reconcile(self):
        self.assertEqual(ledger.reconcile([10.0, 5.0], [12.0]), 3.0)


if __name__ == "__main__":
    unittest.main()
