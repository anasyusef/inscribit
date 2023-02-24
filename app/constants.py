import os
from enum import Enum
from pathlib import Path

SATS_PER_BTC = 100_000_000
STORAGE_PATH = os.path.join(os.path.dirname(Path(__file__).resolve().parent), "storage")
PROCESSED_PATH = os.path.join(os.path.dirname(Path(__file__).resolve().parent), "processed")
print("Creating directories if they don't exist")
Path(STORAGE_PATH).mkdir(parents=True, exist_ok=True)
Path(PROCESSED_PATH).mkdir(parents=True, exist_ok=True)

FILE_EXTS = {
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".asc": "application/pgp-signature",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".flac": "audio/flac",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".apng": "image/apng",
    ".avif": "image/avif",
    ".gif": "image/gif",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".glb": "model/gltf-binary",
    ".stl": "model/stl",
    ".html": "text/html;charset=utf-8",
    ".txt": "text/plain;charset=utf-8",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}


class Status(str, Enum):
    
    PAYMENT_PENDING = "payment_pending"
    PAYMENT_RECEIVED_UNCONFIRMED = "payment_received_unconfirmed"
    PAYMENT_RECEIVED_CONFIRMED = "payment_received_confirmed"
    PAYMENT_UNDERPAID = "payment_underpaid"
    PAYMENT_OVERPAID = "payment_overpaid"
    PAYMENT_OVERPAID_CONFIRMED = "payment_overpaid_confirmed"
    BROADCASTED = "broadcasted"
    BROADCASTED_CONFIRMED = "broadcasted_confirmed"
    INSCRIPTION_SENT = "inscription_sent"
    INSCRIPTION_SENT_CONFIRMED = "inscription_sent_confirmed"


class Chain(str, Enum):
    MAINNET = "mainnet"
    SIGNET = "signet"
