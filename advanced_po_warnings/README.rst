================================
Advanced Purchase Order Warnings
================================

Odoo 16 (Community & Enterprise) module that checks purchase orders when they are
confirmed and either warns or blocks them, with a supervisor review workflow.

Configuration
=============
*Purchase > Configuration > Settings > Advanced Order Warnings* (company-specific).

1. Tick **Advanced Purchase Order Warnings** (off by default).
2. Optionally pick the **Review Activity Users** (must be PO Warnings Supervisors).
   Empty = every supervisor of the company.
3. Enable and tune any of the three rules. Each has its own **Enforcement**:
   *Warning only* or *Warning + block confirmation*.

Order Duplicate
   Same product set (quantities in product UoM, split lines summed) on another
   non-cancelled order. Vendor scope: same / different / any. Period: N days before or
   after the order date. *Match quantities* off = same products, any quantity.

Item Reorder Frequency
   A product already appears on at least *threshold* other orders within N days.
   Documents: confirmed orders (incl. *To Approve*) or RFQs + orders. Vendor scope as above.
   One alert per product.

Destination
   Monitor specific **locations** (sub-locations included) or **warehouses**
   (empty = all). Flags the order when another order to the same destination exists
   within N days. **N = 0** flags every order to a monitored destination (restricted
   destination).

Vendors are compared by commercial entity, so a vendor's contacts count as the vendor.

Security
========
New group **Purchase Order Warnings / Supervisor** (implies Purchase User; Administrator
is a member by default). Only supervisors see *Purchase > Orders > Blocked Orders* and
*Order Warnings Log*, and can bypass or reject. The review fields cannot be written
directly (not even by supervisors) - only through the review workflow.

Workflow
========
* **Warning only**: the order is confirmed; alerts are logged on the *Order Warnings*
  tab and in the chatter.
* **Block**: confirmation stops, the order is flagged *Blocked*, a detailed alert is
  posted and a *PO Warning Review* activity is scheduled for each reviewer.
* **Bypass & Confirm** (supervisor, justification required): the order is confirmed
  (still subject to the standard double validation); activities are marked done.
* **Reject** (supervisor, justification required): the order is cancelled and flagged
  *Rejected*; the buyer is notified.
* Fixing the order and confirming again re-runs the checks and unblocks it
  automatically. Setting a rejected order back to draft requires a new review.
* A live preview banner on draft orders shows what confirmation will trigger.

Performance
===========
Each rule runs **one set-based SQL query per company** for the whole batch of orders
(orders are passed as arrays and ``unnest``-ed), so the query count does not grow with
the number of orders (covered by a test). An index on ``purchase_order(company_id,
date_order)`` supports the period filters.

Tests
=====
``odoo-bin -d <db> -i advanced_po_warnings --test-enable --test-tags /advanced_po_warnings``
