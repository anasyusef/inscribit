from constants import Status, SATS_PER_BTC
from datetime import datetime
from config import supabase
import json
from config import session

def parse_transaction_data(data):
    result = data.get("result")

    amount = result["amount"]
    confirmations = result["confirmations"]
    time_received = result["timereceived"]

    details = result["details"][
        0
    ]  # Assuming there is only one item in details that contains the address

    address = details["address"]

    return {
        "amount": amount,
        "confirmations": confirmations,
        "time_received": time_received,
        "address": address,
    }


def update_order_status(order_id: int, status: Status):
    return (
        supabase.table("order").update({"status": status}).eq("id", order_id).execute()
    )


def get_status(payable_amount, amount_received):
    """
    Returns the status based on the amount received vs payable amount
    """
    if amount_received < payable_amount:
        return Status.PAYMENT_UNDERPAID
    elif amount_received > payable_amount:
        return Status.PAYMENT_OVERPAID
    return Status.PAYMENT_RECEIVED


def get_transaction(tx_id: str):
    data = {
        "jsonrpc": "2.0",
        "method": "gettransaction",
        "params": [tx_id],
    }

    response = session.post(
        "http://localhost:8332",
        data=json.dumps(data),
        headers={"Content-Type": "application/json"},
    )
    return response.json()


def insert_tx(order_id, tx_id, data):
    return (
        supabase.table("tx")
        .insert(
            {
                "order_id": order_id,
                "tx_hash": tx_id,
                # "amount_sats": 1,
                "amount_sats": int(data["amount"] * SATS_PER_BTC),
                "confirmations": data["confirmations"],
                "received_at": datetime.fromtimestamp(data["time_received"]).strftime(
                    "%Y/%m/%d %H:%M:%S"
                ),
            }
        )
        .execute()
    )


def get_all_txs_from_order_id(order_id):
    data = supabase.table("tx").select("*").eq("order_id", order_id).execute()
    return data.data


def get_order_with_assigned_address(address: str):
    return (
        supabase.table("order")
        .select("*")
        .eq(
            "assigned_taproot_address",
            address,
        )
        .limit(1)
        .execute()
    )