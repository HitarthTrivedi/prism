# Prism — Demo Pack & Manufacturing Guide

Everything here is fake data built for demos and testing. Safe to run, safe
to show a client.

**All demo email addresses end in `@example.com`** — a reserved domain that
can never deliver to a real person. You can run a full `/email` test without
any risk of mailing someone by accident.

---

## 0. Start here — the one-file demo

**`demo_stock_check.csv`** — the file to use if you only use one. Parts
required *and* stock on hand in a single sheet, so it works today with no
add-on: the AI just reads one table.

**Prompt:**

> *"We are building 500 boards. From this sheet, work out what we already
> have enough of and what we must purchase — show quantity to buy per part
> with a 3% wastage allowance, group the purchase list by supplier, and give
> the total purchase value."*

**The correct answer — 9 in stock, 9 short, ₹1,49,438 to purchase:**

| Part | Need | Have | Buy | Supplier | Value ₹ |
|---|---:|---:|---:|---|---:|
| STM32F103C8T6 | 515 | 120 | 395 | Sanghvi Electronics | 71,890 |
| CAP-10UF-0805 | 3090 | 900 | 2190 | Shakti Traders | 2,409 |
| CONN-RJ45-8P | 515 | 60 | 455 | Nirmal Components | 24,798 |
| XTAL-8MHZ | 515 | 410 | 105 | Vikas Enterprise | 1,302 |
| BUZZ-5V | 515 | 0 | 515 | Nirmal Components | 9,785 |
| RELAY-5V-SPDT | 1030 | 140 | 890 | Sanghvi Electronics | 33,820 |
| HDR-2X5-254 | 515 | 0 | 515 | Shakti Traders | 4,017 |
| HEATSINK-TO220 | 515 | 450 | 65 | Patel Metals | 403 |
| STANDOFF-M3-10 | 2060 | 1800 | 260 | Patel Metals | 1,014 |

*(Need = per-board × 500 × 1.03, rounded up.)*

Two parts — `BUZZ-5V` and `HDR-2X5-254` — have **zero stock**. If a run
doesn't flag those, it dropped them, and that's the failure that costs a
delivery date. Check those two first.

---

## 1. The files

| File | What it is |
|---|---|
| `demo_bom.csv` | A customer's parts requirement — 14 lines, per board |
| `demo_inventory.csv` | Their stock, 18 lines, across bins |
| `demo_purchase_orders.csv` | 10 POs — some closed, some part-received, some pending |
| `demo_attendance.csv` | 5 workers × 3 days, with late/absent/leave/overtime |
| `demo_dispatch.csv` | 7 dispatches — delivered, in transit, pending pickup |
| `demo_bills.csv` | Receivables + payables, including 2 overdue |
| `demo_recipients.csv` | 6 safe demo email addresses |

The BOM and inventory files contain **deliberate traps**, so a demo actually
proves something instead of looking clever:

- `LM358N` vs `lm358n` — case difference
- `10K-0603` vs `10K 0603` — hyphen vs space
- `100N-0603` vs `100n0603` — case *and* separator
- `10K 0603` appears **twice** (two bins) — must be added together, not
  counted once
- `BUZZ-5V` and `HDR-2X5-254` are **not in inventory at all**
- Inventory has extra items not on the BOM — must be ignored

---

## 2. The correct answer (check the demo against this)

For **500 boards at 3% wastage**, computed independently:

**7 lines in stock · 7 short**

| Part | Need | Have | Must buy |
|---|---:|---:|---:|
| STM32F103C8T6 | 515 | 120 | 395 |
| CAP-10UF-0805 | 3090 | 900 | 2190 |
| CONN-RJ45-8P | 515 | 60 | 455 |
| XTAL-8MHZ | 515 | 410 | 105 |
| BUZZ-5V | 515 | 0 | **515** — not in inventory |
| RELAY-5V-SPDT | 1030 | 140 | 890 |
| HDR-2X5-254 | 515 | 0 | **515** — not in inventory |

If a run gets `10K-0603` wrong, it failed the duplicate-bin trap. If it
misses `BUZZ-5V`, it silently dropped a line — the exact failure that costs
a factory a delivery date.

---

## 3. The client's four requirements — honest status

> *"Dispatch process, attendance, POs tracking and making, tracking the bills
> for the month"*

All four are the same shape: **read a CSV → work something out → write a
report**. The arithmetic must be done by code; the AI writes it up. That
split is what makes the numbers checkable.

### ✅ Works today (spec mode, no add-on needed)

These produce a real written report from an attached CSV:

**Monthly bills report**
- Attach: `demo_bills.csv`
- Prompt: *"Prepare a monthly outstanding report from this bill register —
  separate receivables from payables, list overdue items first with days
  overdue, and total each group."*

**Dispatch status report**
- Attach: `demo_dispatch.csv`
- Prompt: *"Prepare a dispatch status report — group by delivery status,
  flag anything pending pickup or without an invoice number, and summarise
  by customer."*

**Attendance summary**
- Attach: `demo_attendance.csv`
- Prompt: *"Prepare a monthly attendance summary per employee — present,
  late, absent and leave days, plus who worked past their shift end."*

⚠️ **Caveat, and say it to the client:** the AI is doing the counting here.
For a 15-row demo it will be right. For a real 2,000-row month, the totals
must be computed by code before the AI writes them up — same reason `/boq`
measures drawings instead of asking an AI to eyeball them. Treat these as
report *drafting*, not as an accounting system.

### ⚠️ Needs the BOM/PO matcher add-on (not built yet)

**PO vs inventory comparison**
This is the one the client actually cares about, and it's the one that must
not be guessed. Two files, matched line by line, shortage reported.

Once built:
- Attach: `demo_purchase_orders.csv` + `demo_inventory.csv`
- Output: what's on order, what's arrived, what's still short, what to chase

**PO making**
Generating the PO document itself from the shortage list — a natural second
step from the same matcher.

---

## 4. Testing the parts you have today

### BOQ from a written spec (no drawing)
1. Prism GUI → **ADD-ONS → BOQ**
2. Leave the drawing box empty
3. Attach `demo_bom.csv` with **Add file**
4. Type: *"Quote the materials to build 500 units of this board, list what
   we must buy"*
5. Press **Write the BOQ**

Check the output against §2 above. Where it disagrees, that's the case for
building the matcher properly — useful either way.

### BOQ from a drawing
1. **ADD-ONS → BOQ** → Browse to any `.dwg`/`.dxf`
2. Wait for **Measured quantities** to fill in
3. Type what the BOQ should cover, press **Write the BOQ**

### Email blast
1. **ADD-ONS → Email**
2. Attach `demo_recipients.csv`
3. Goal: *"invite them to see a demo of our automation system next week"*
4. Review the draft, then send — all addresses are `@example.com`, so
   nothing leaves for a real inbox

---

## 5. What to tell this client

**Say:** *"Your bills, dispatch and attendance reports — Prism writes those
from your CSVs today. Your PO-versus-stock check is a two-week add-on, and
it's the one that saves you the most, because it's the one nobody can afford
to get wrong."*

**Don't say** Prism replaces their ERP or does their accounts. It reads what
they already keep and writes the documents they'd otherwise type by hand.

That's a smaller promise, and it's one that survives the first month.
