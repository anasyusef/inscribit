from enum import Enum

SATS_PER_BTC = 100_000_000

class Status(str, Enum):
    
    PAYMENT_PENDING = "payment_pending"
    PAYMENT_RECEIVED = "payment_received"
    PAYMENT_UNDERPAID = "payment_underpaid"
    PAYMENT_OVERPAID = "payment_overpaid"
