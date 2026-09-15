"""Per-sender extraction templates, learned by reading real snippets.

Every pattern below was written against messages actually present in
/tmp/mc-snap.sqlite; none is speculative. Evidence for each sender is in
`records/2026-08-24-ledger-extraction-prototype/work/template-source-snippets.txt`.

The unit of learning is the SENDER. Learning "NJ TRANSIT MyTix - Receipt ->
purchase, ref at 'Trx Seq Id :', date at 'Ticket Purchase on'" is one decision
that then serves all 119 messages from that address, and every future one.

Rule shape:
    match      regex; `match_on` selects subject (default) | snippet | both
    action     'extract' (needs kind) or 'terminate'
    kind       purchase|subscription|booking|appointment|payment|shipment
    fields     name -> {rx, on, group}   pulled from the message
    <field>    "$N" takes group N of the rule's own match
    confidence how much of a *verifiable* row the snippet yields

A `terminate` rule is as valuable as an extract rule: it is how volume
actually drops. "a family member requests $140.00" is not money moving.
"""
from __future__ import annotations

from cascade import Template

# Reused sub-patterns.
USD = r"\$\s?[\d,]+(?:\s?\.\s?\d{2})?"
AMZ_ORDER = r"\b(\d{3}-\d{7}-\d{7})\b"

RAW: dict[str, dict] = {

    # ---- food delivery ------------------------------------------------
    "orders@eat.grubhub.com": {"notes": "one shape only; restaurant in subject, no amount in snippet", "rules": [
        {"name": "order", "match": r"^Thanks for your (.+?) order$", "action": "extract",
         "kind": "purchase", "counterparty": "$1", "confidence": 0.75,
         "fields": {"description": {"rx": r"(ETA [\d: APM–-]+)", "on": "snippet"}}},
    ]},
    "no-reply@grubhub.com": {"rules": [
        {"name": "status", "match": r"Your order was (updated|canceled)", "action": "terminate",
         "why": "status change on an order already recorded"},
    ]},
    # ---- Amazon: three senders, one purchase ---------------------------
    "auto-confirm@amazon.com": {"notes": "order # usually truncated out of the snippet; quoted title is the link key", "rules": [
        {"name": "order-conf", "match": r"Your Amazon\.com order", "action": "extract",
         "kind": "purchase", "confidence": 0.7,
         "fields": {"ref_number": {"rx": AMZ_ORDER, "on": "both"},
                    "description": {"rx": r'(?:order of )?("[^"]+")', "on": "subject"},
                    "service_dates": {"rx": r"Arriving: ([^S]{3,40}?) Ship to", "on": "snippet"}}},
        {"name": "ordered", "match": r"^Ordered:", "action": "extract",
         "kind": "purchase", "confidence": 0.55,
         "fields": {"ref_number": {"rx": AMZ_ORDER, "on": "both"},
                    "description": {"rx": r"^Ordered:\s*(.+)$", "on": "subject"}}},
    ]},
    "shipment-tracking@amazon.com": {"rules": [
        {"name": "shipped", "match": r"^Shipped:|has shipped|out for delivery", "action": "extract",
         "kind": "shipment", "confidence": 0.7,
         "fields": {"ref_number": {"rx": AMZ_ORDER, "on": "both"},
                    "description": {"rx": r"^(?:Shipped:\s*)?(.+)$", "on": "subject"}}},
    ]},
    "order-update@amazon.com": {"rules": [
        {"name": "delivered", "match": r"^Delivered:", "action": "extract",
         "kind": "shipment", "confidence": 0.7,
         "fields": {"ref_number": {"rx": AMZ_ORDER, "on": "both"},
                    "description": {"rx": r"^Delivered:\s*(.+)$", "on": "subject"}}},
        {"name": "rate", "match": r"will you rate|ever wonder if your reviews", "action": "terminate",
         "why": "review solicitation"},
    ]},

    # ---- Apple ---------------------------------------------------------
    "no_reply@email.apple.com": {"notes": "two receipt layouts; the older ALL-CAPS one drops the amount past the snippet cut", "rules": [
        {"name": "receipt-subscription",
         "match": r"Renews [A-Z][a-z]{2}|\(Monthly\)|Renewal Notice|AppleCare\+|Monthly\)",
         "match_on": "snippet", "action": "extract", "kind": "subscription", "confidence": 0.85,
         "fields": {"ref_number": {"rx": r"ORDER ID:?\s*([A-Z0-9]{8,12})", "on": "snippet"},
                    "amount": {"rx": r"(\$\s?\d[\d,]*\.\d{2})", "on": "snippet"},
                    "date": {"rx": r"(?:^Receipt(?: & Renewal Notice)?\s+|DATE\s+)([A-Z][a-z]{2,8}\.? \d{1,2}, 20\d{2})", "on": "snippet"},
                    "description": {"rx": r"\S+@\S+\s+(.{3,60}?)(?:\s+Renews|\s+Billing|$)", "on": "snippet"},
                    "service_dates": {"rx": r"Renews ([A-Z][a-z]{2,8} \d{1,2}, 20\d{2})", "on": "snippet"}}},
        {"name": "receipt-purchase", "match": r"^Your receipt from Apple", "match_on": "subject",
         "action": "extract", "kind": "purchase", "confidence": 0.6,
         "notes": "the ALL-CAPS layout pushes the amount and the 'Renews' line past the snippet cut, so subscription-vs-purchase is not decidable here -- this is the model lane's job",
         "fields": {"ref_number": {"rx": r"ORDER ID:?\s*([A-Z0-9]{8,12})", "on": "snippet"},
                    "amount": {"rx": r"(\$\s?\d[\d,]*\.\d{2})", "on": "snippet"},
                    "date": {"rx": r"(?:^Receipt(?: & Renewal Notice)?\s+|DATE\s+)([A-Z][a-z]{2,8}\.? \d{1,2}, 20\d{2})", "on": "snippet"},
                    "description": {"rx": r"DOCUMENT NO\. \d+ (.{3,50})$", "on": "snippet"}}},
        {"name": "sub-confirm", "match": r"Subscription (?:Confirmation|is Confirmed)", "action": "extract",
         "kind": "subscription", "confidence": 0.6,
         "fields": {"description": {"rx": r"^Subscription Confirmation (.{3,50}?)(?: \$|Dear|$)", "on": "snippet"},
                    "amount": {"rx": r"(\$\d[\d,]*\.\d{2})", "on": "snippet"}}},
        {"name": "sub-notice", "match": r"Subscription is Expiring|Price Increase|Renewal Notice for",
         "action": "terminate", "why": "notice about a subscription; no money moved"},
        {"name": "applecare", "match": r"AppleCare\+ Proof of Coverage", "action": "terminate",
         "why": "coverage document, billed separately in a receipt"},
        {"name": "device", "match": r"Find My|A sound was played|was signed in to|Your Apple Account",
         "action": "terminate", "why": "device/security notice"},
    ]},
    "googleplay-noreply@google.com": {"rules": [
        {"name": "play-receipt", "match": r"Your Google Play Order Receipt from (.+)$", "action": "extract",
         "kind": "subscription", "date": "$1", "confidence": 0.7,
         "fields": {"ref_number": {"rx": r"Order number:\s*(SOP\.[\w.\-]+)", "on": "snippet"},
                    "description": {"rx": r"(Your subscription from [A-Za-z ]+)", "on": "snippet"}}},
    ]},

    # ---- payment rails: counterparty is not the sender -------------------
    "venmo@venmo.com": {"notes": "subject is authoritative for direction; the snippet sometimes leads with the reverse phrasing", "rules": [
        {"name": "you-paid", "match": r"^You paid (.+?) (\$[\d,]+\.\d{2})$", "action": "extract",
         "kind": "payment", "counterparty": "$1", "amount": "$2", "confidence": 0.95,
         "fields": {"ref_number": {"rx": r"Transaction ID (\d+)", "on": "snippet"},
                    "date": {"rx": r"Date ([A-Z][a-z]{2} \d{1,2}, 20\d{2})", "on": "snippet"},
                    "description": {"rx": r"\$\s?[\d,]+\s?\.\s?\d{2}\s+(.{1,30}?)\s+See transaction", "on": "snippet"}}},
        {"name": "request", "match": r"requests \$", "action": "terminate",
         "why": "a request is not a payment"},
        {"name": "comment", "match": r"commented on a payment", "action": "terminate",
         "why": "social notice on an existing payment"},
        {"name": "history", "match": r"transaction history", "action": "terminate",
         "why": "monthly statement pointer"},
    ]},
    "service@paypal.com": {"notes": "PayPal is the rail; the merchant is in the snippet", "rules": [
        {"name": "you-paid", "match": r"You paid (\$[\d,.]+)(?: USD)? to (.+?)(?: Transaction| Merchant|$)",
         "match_on": "snippet", "action": "extract", "kind": "payment",
         "amount": "$1", "counterparty": "$2", "confidence": 0.95,
         "fields": {"ref_number": {"rx": r"(?:Transaction ID|Order ID)\s+([A-Z0-9]{6,40})", "on": "snippet"},
                    "date": {"rx": r"Transaction date ([A-Z][a-z]{2,8} \d{1,2}, 20\d{2})", "on": "snippet"},
                    "description": {"rx": r"(?:View Payment Details\s+)?([A-Z][A-Za-z ]{3,30})\s+Qty:", "on": "snippet"}}},
        {"name": "authorized", "match": r"You authorized (\$[\d,.]+)(?: USD)? to (.+?)(?: Merchant|$)",
         "match_on": "snippet", "action": "extract", "kind": "payment",
         "amount": "$1", "counterparty": "$2", "confidence": 0.9,
         "fields": {"ref_number": {"rx": r"Order ID (\S{6,40})", "on": "snippet"},
                    "date": {"rx": r"Transaction date ([A-Z][a-z]{2,8} \d{1,2}, 20\d{2})", "on": "snippet"}}},
        {"name": "autopay-sent", "match": r"payment to (.+?)\s*$", "match_on": "subject",
         "action": "extract", "kind": "payment", "counterparty": "$1", "confidence": 0.5,
         "fields": {"description": {"rx": r"automatic payment for ([^.]{3,60})", "on": "snippet"}}},
        {"name": "autopay-change", "match": r"autopay|automatic payment status", "action": "terminate",
         "why": "autopay setup change, no money moved"},
        {"name": "account", "match": r"account statement|legal agreement|phone number|card information|"
                                     r"Pay in 4|Pay Later|policy", "action": "terminate",
         "why": "account/marketing notice"},
    ]},
    "quickbooks@notification.intuit.com": {"notes": "how Acme Lawn bills Caio; invoice and payment share the invoice number", "rules": [
        {"name": "payment-confirm", "match": r"You paid (\$[\d,.]+) to (.+?) on (\d{2}/\d{2}/\d{4})",
         "match_on": "snippet", "action": "extract", "kind": "payment",
         "amount": "$1", "counterparty": "$2", "date": "$3", "confidence": 0.95,
         "fields": {"ref_number": {"rx": r"Invoice no\.\s*(\d+)", "on": "snippet"}}},
        {"name": "invoice-balance", "match": r"invoice (\d+)", "match_on": "subject", "action": "extract",
         "kind": "payment", "ref_number": "$1", "confidence": 0.55,
         "fields": {"amount": {"rx": r"BALANCE DUE(\$[\d,.]+)|DUE \d{2}/\d{2}/\d{4} (\$[\d,.]+)", "on": "snippet"},
                    "counterparty": {"rx": r"(?:from|to|DETAILS )\s*(Acme Lawn[^.]*)", "on": "both"}}},
        {"name": "invoice-nonum", "match": r"Invoice \d+|INVOICE (\d+) DETAILS", "match_on": "both",
         "action": "extract", "kind": "payment", "confidence": 0.5,
         "fields": {"ref_number": {"rx": r"[Ii]nvoice #?(\d{4,6})", "on": "both"},
                    "amount": {"rx": r"BALANCE DUE(\$[\d,.]+)|DUE \d{2}/\d{2}/\d{4} (\$[\d,.]+)", "on": "snippet"},
                    "counterparty": {"rx": r"(Acme Lawn[^.]*)", "on": "both"}}},
    ]},
    "customerservice@ezpassnj.com": {"rules": [
        {"name": "replenish", "match": r"replenishment payment of (\$[\d,.]+) has been applied",
         "match_on": "snippet", "action": "extract", "kind": "payment", "amount": "$1",
         "counterparty": "E-ZPass New Jersey", "confidence": 0.9,
         "fields": {"date": {"rx": r"(?:Date: )?(\d{2}/\d{2}/\d{4})", "on": "snippet"}}},
        {"name": "other", "match": r"Replenishment Update|Parking Transaction|Statement|Violation",
         "action": "terminate", "why": "account notice, not a payment"},
    ]},
    # ---- travel and bookings --------------------------------------------
    "receipts@united.com": {"rules": [
        {"name": "eticket", "match": r"eTicket Itinerary and Receipt for Confirmation (\w{6})",
         "action": "extract", "kind": "booking", "ref_number": "$1", "confidence": 0.75,
         "fields": {"date": {"rx": r"^([A-Z][a-z]{2}, [A-Z][a-z]{2} \d{2}, 20\d{2})", "on": "snippet"}}},
        {"name": "purchase", "match": r"Thanks for your purchase with United", "action": "extract",
         "kind": "booking", "counterparty": "United Airlines", "confidence": 0.7,
         "fields": {"date": {"rx": r"^([A-Z][a-z]{2}, [A-Z][a-z]{2} \d{1,2}, 20\d{2})", "on": "snippet"},
                    "service_dates": {"rx": r"(UA\d+ [A-Z][a-z]{2}, [A-Z][a-z]{2} \d{1,2}, 20\d{2}(?: [A-Z][a-z]{2}, [A-Z][a-z]{2} \d{1,2}, 20\d{2})?)", "on": "snippet"}}},
        {"name": "refund", "match": r"refund", "action": "terminate",
         "why": "refund of a purchase already in the ledger; negative amounts are out of scope"},
    ]},
    "notifications@united.com": {"rules": [
        {"name": "booking", "match": r"booking confirmation\s*[–-]\s*(\w{6})", "action": "extract",
         "kind": "booking", "ref_number": "$1", "counterparty": "United Airlines", "confidence": 0.7},
        {"name": "trip", "match": r"trip to|Check in|reminders about", "action": "terminate",
         "why": "pre-trip reminder for a booking already recorded"},
    ]},
    "avis@e.avis.com": {"rules": [
        {"name": "confirm", "match": r"Avis Rental Confirmation: (\d+)", "action": "extract",
         "kind": "booking", "ref_number": "$1", "counterparty": "Avis", "confidence": 0.75},
        {"name": "ereceipt", "match": r"Your E-receipt From Avis", "action": "extract",
         "kind": "payment", "counterparty": "Avis", "confidence": 0.4},
        {"name": "reminder", "match": r"E-Reminder for Avis Reservation|has been canceled|account statement",
         "action": "terminate", "why": "reminder/cancel notice on an existing reservation"},
    ]},
    "express@airbnb.com": {"rules": [
        {"name": "host-msg", "match": r"Reservation (?:for|at) (.+?),? (?:for )?([A-Z][a-z]{2} \d{1,2} ?[–-] ?\d{1,2}(?:, 20\d{2})?)",
         "action": "extract", "kind": "booking", "counterparty": "$1", "service_dates": "$2",
         "date_from_service": True, "confidence": 0.6},
        {"name": "inquiry", "match": r"Inquiry at", "action": "terminate", "why": "inquiry, not a booking"},
    ]},
    "automated@airbnb.com": {"rules": [
        {"name": "receipt", "match": r"trip, here's your Airbnb receipt|Your receipt from Airbnb",
         "action": "extract", "kind": "payment", "counterparty": "Airbnb", "confidence": 0.6,
         "fields": {"ref_number": {"rx": r"Receipt ID: ([A-Z0-9]{6,14})", "on": "snippet"},
                    "date": {"rx": r"Receipt ID: [A-Z0-9]+ . ([A-Z][a-z]{2,8} \d{1,2}, 20\d{2})", "on": "snippet"},
                    "service_dates": {"rx": r"((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), [A-Z][a-z]{2} \d{1,2}, 20\d{2} -> (?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), [A-Z][a-z]{2} \d{1,2}, 20\d{2})", "on": "snippet"},
                    "description": {"rx": r"(\d+ nights? in [A-Z][A-Za-z ]{2,24})", "on": "snippet"}}},
        {"name": "confirmed", "match": r"Confirmed: Your reservation for (.+)$", "action": "extract",
         "kind": "booking", "service_dates": "$1", "confidence": 0.7,
         "fields": {"counterparty": {"rx": r"You're all set for (\w[\w ]{2,24}?) Airbnb", "on": "snippet"}}},
        {"name": "noise", "match": r"Account activity|Write a review|reminder|didn't respond|Welcome to",
         "action": "terminate", "why": "account notice or review nudge"},
    ]},
    "noreply@resy.com": {"rules": [
        {"name": "confirmed", "match": r"Your reservation at (.+?) is confirmed", "action": "extract",
         "kind": "booking", "counterparty": "$1", "confidence": 0.85,
         "fields": {"service_dates": {"rx": r"(?:Reservation booked\.(?: via Reserve with Google)?\s*)(?:.+?)((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,? [A-Z][a-z]{2}\.? \d{1,2} at \d{1,2}:\d{2}[ap]m)", "on": "snippet"},
                    "description": {"rx": r"(\d+ Guests?, [A-Za-z ]+)", "on": "snippet"}}},
        {"name": "cancel", "match": r"has been cancelled|has been changed", "action": "terminate",
         "why": "cancellation/change of a reservation already recorded"},
    ]},
    "reserve-noreply@google.com": {"rules": [
        {"name": "confirmed", "match": r"Your reservation at (.+?) is confirmed", "action": "extract",
         "kind": "booking", "counterparty": "$1", "confidence": 0.75},
        {"name": "other", "match": r"canceled|in progress", "action": "terminate",
         "why": "cancel/pending notice"},
    ]},
    "no-reply@opentable.com": {"rules": [
        {"name": "confirmed", "match": r"Your [Rr]eservation [Cc]onfirmation for (.+)$", "action": "extract",
         "kind": "booking", "counterparty": "$1", "confidence": 0.7},
        {"name": "other", "match": r"cancellation|Confirm It's You|How was|Let us know",
         "action": "terminate", "why": "cancel, OTP or feedback"},
    ]},
    "calendar-notification@google.com": {"notes": "calendar is a second witness to bookings Resy/OpenTable also report; linked on counterparty+date", "rules": [
        {"name": "reservation", "match": r"Reservation at (.+?) @ (.+?) \(", "action": "extract",
         "kind": "booking", "counterparty": "$1", "service_dates": "$2", "date_from_service": True, "confidence": 0.6},
        {"name": "stay", "match": r"Stay at (.+?) @ (.+?) \(", "action": "extract",
         "kind": "booking", "counterparty": "$1", "service_dates": "$2", "date_from_service": True, "confidence": 0.6},
        {"name": "flight", "match": r"(Flight to .+?) @ (.+?) \(", "action": "extract",
         "kind": "booking", "counterparty": "$1", "service_dates": "$2", "date_from_service": True, "confidence": 0.6},
        {"name": "appointment-with", "match": r"(?:Notification|Reminder): (?:Appointment|Consultation|Connect) with (.+?) @ (.+?) \(",
         "action": "extract", "kind": "appointment", "counterparty": "$1", "service_dates": "$2",
         "date_from_service": True, "confidence": 0.6},
        {"name": "appointment", "match": r"(?:Notification|Reminder): (.+?) @ (.+?) \(", "action": "extract",
         "kind": "appointment", "counterparty": "$1", "service_dates": "$2",
         "date_from_service": True, "confidence": 0.45},
    ]},

    # ---- retail / subscriptions ------------------------------------------
    "noreply@stubhub.com": {"rules": [
        {"name": "order", "match": r"Thanks for your order - Order #(\d+)", "action": "extract",
         "kind": "purchase", "ref_number": "$1", "counterparty": "StubHub", "confidence": 0.75},
        {"name": "delivered", "match": r"tickets were delivered for order# (\d+)", "action": "extract",
         "kind": "shipment", "ref_number": "$1", "counterparty": "StubHub", "confidence": 0.8},
        {"name": "other", "match": r"listing|feedback|How did we do|Payment method|Accept your|Enjoy the event",
         "action": "terminate", "why": "selling-side or survey mail"},
    ]},
    "invoice+statements@mail.anthropic.com": {"rules": [
        {"name": "receipt", "match": r"Your receipt from Anthropic, PBC #([\d-]+)", "action": "extract",
         "kind": "subscription", "ref_number": "$1", "counterparty": "Anthropic", "confidence": 0.6},
    ]},
    "sales.us@jetbrains.com": {"rules": [
        {"name": "license", "match": r"License Certificate for (.+?) /\s*(?:Automatic )?[Oo]rder (A\d+)",
         "action": "extract", "kind": "subscription", "description": "$1", "ref_number": "$2",
         "counterparty": "JetBrains", "confidence": 0.75},
        {"name": "credits", "match": r"balance topped up! Order (A\d+) confirmed", "action": "extract",
         "kind": "purchase", "ref_number": "$1", "counterparty": "JetBrains", "confidence": 0.75},
        {"name": "reminder", "match": r"expired|Renewal reminder|Payment date", "action": "terminate",
         "why": "renewal nudge, no charge"},
    ]},
    "billing.us@jetbrains.com": {"rules": [
        {"name": "invoice", "match": r"JetBrains: Invoice (JBAINV\d+)", "action": "extract",
         "kind": "payment", "counterparty": "JetBrains", "confidence": 0.75,
         "fields": {"ref_number": {"rx": r"purchase (A\d+)", "on": "snippet"},
                    "description": {"rx": r"(JBAINV\d+)", "on": "subject"}}},
    ]},
    "no-reply@lyftmail.com": {"rules": [
        {"name": "ride", "match": r"Your ride with (.+?) on (.+)$", "action": "extract",
         "kind": "purchase", "description": "$1", "date": "$2", "counterparty": "Lyft",
         "confidence": 0.5},
        {"name": "other", "match": r"Confirm your email|offer|off", "action": "terminate",
         "why": "account or promo"},
    ]},
    "noreply@mytix.njtransit.com": {"notes": "two receipt layouts, same fields; the Amount column falls past the snippet cut", "rules": [
        {"name": "mytix", "match": r"Your Ticket Purchase on ([\d/]+)", "match_on": "snippet",
         "action": "extract", "kind": "purchase", "date": "$1", "counterparty": "NJ TRANSIT",
         "confidence": 0.8,
         "fields": {"ref_number": {"rx": r"Trx Seq Id\s*:\s*(\d+)", "on": "snippet"},
                    "description": {"rx": r"Ticket No\.\(s\): ([\d,]{5,60})", "on": "snippet"}}},
        {"name": "receipt2", "match": r"Receipt for Purchase on ([\d/]+)", "match_on": "snippet",
         "action": "extract", "kind": "purchase", "date": "$1", "counterparty": "NJ TRANSIT",
         "confidence": 0.8,
         "fields": {"ref_number": {"rx": r"Transaction Seq ID:\s*(\d+)", "on": "snippet"},
                    "description": {"rx": r"Ticket No\.\(s\): ([\d,]{5,60})", "on": "snippet"}}},
        {"name": "api", "match": r"API userid", "action": "terminate", "why": "developer account mail"},
    ]},
    "no.reply.alerts@chase.com": {"rules": [
        {"name": "payment-scheduled", "match": r"Your credit card payment is scheduled", "action": "extract",
         "kind": "payment", "confidence": 0.8,
         "fields": {"amount": {"rx": r"Amount (\$[\d,.]+)", "on": "snippet"},
                    "counterparty": {"rx": r"Account ([A-Za-z ]+?)\(", "on": "snippet"}}},
        {"name": "notices", "match": r"statement is available|payment is due on|digital wallet|Privacy Notice",
         "action": "terminate", "why": "statement or due-date notice; no money moved"},
    ]},
}


def load() -> dict[str, Template]:
    out: dict[str, Template] = {}
    for sender, spec in RAW.items():
        rules = [r for r in spec["rules"]]
        # An 'escalate' action is expressed as a rule with no kind; the
        # interpreter treats a missing rule as the sender default, so lift it.
        out[sender] = Template(sender, [r for r in rules if r["action"] != "escalate"],
                               default=("escalate" if any(r["action"] == "escalate" for r in rules)
                                        else spec.get("default", "terminate")),
                               notes=spec.get("notes", ""))
    return out
