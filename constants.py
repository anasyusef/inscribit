from enum import Enum
import os
from pathlib import Path

SATS_PER_BTC = 100_000_000
STORAGE_PATH = os.path.join(Path.home(), "inscription-service", "storage")


class Status(str, Enum):
    PAYMENT_PENDING = "payment_pending"
    PAYMENT_RECEIVED = "payment_received"
    PAYMENT_UNDERPAID = "payment_underpaid"
    PAYMENT_OVERPAID = "payment_overpaid"
