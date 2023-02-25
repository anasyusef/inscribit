import json
from datetime import datetime

import postgrest.exceptions

from .config import PORTS, session, supabase
from .constants import SATS_PER_BTC, Chain, Status


def parse_transaction_data(data):
    result = data.get("result")

    amount = result["amount"]
    confirmations = result["confirmations"]
    time_received = result["timereceived"]

    return {
        "amount": amount,
        "confirmations": confirmations,
        "time_received": time_received,
        "details": result["details"],
    }


def update_order_status(order_id: str, status: Status):
    return (
        supabase.table("Order").update({"status": status}).eq("id", order_id).execute()
    )


def get_status(payable_amount, amount_received: dict[str, int]):
    """
    Returns the status based on the amount received vs payable amount
    """
    unconfirmed_received = amount_received["unconfirmed"]
    confirmed_received = amount_received["confirmed"]

    if confirmed_received > payable_amount:
        return Status.PAYMENT_OVERPAID_CONFIRMED

    if (unconfirmed_received + confirmed_received) > payable_amount:
        return Status.PAYMENT_OVERPAID

    if (unconfirmed_received + confirmed_received) < payable_amount:
        return Status.PAYMENT_UNDERPAID

    if unconfirmed_received == payable_amount:
        return Status.PAYMENT_RECEIVED_UNCONFIRMED

    if confirmed_received == payable_amount:
        return Status.PAYMENT_RECEIVED_CONFIRMED


def get_transaction(tx_id: str, chain: Chain = Chain.MAINNET):
    data = {
        "jsonrpc": "2.0",
        "method": "gettransaction",
        "params": [tx_id],
    }
    response = session.post(
        f"http://127.0.0.1:{PORTS[chain]}",
        data=json.dumps(data),
        headers={"Content-Type": "application/json"},
    )
    return response.json()


def upsert_tx(order_id, tx_id, data):
    return (
        supabase.table("Transaction")
        .upsert(
            {
                "order_id": order_id,
                "tx_hash": tx_id,
                # Using round because of floating arithmetic limitations
                "amount_sats": round(data["amount"] * SATS_PER_BTC),
                "confirmations": data["confirmations"],
                "received_at": datetime.fromtimestamp(data["time_received"]).strftime(
                    "%Y/%m/%d %H:%M:%S"
                ),
            }
        )
        .execute()
    )


def get_all_txs_from_order_id(order_id):
    data = supabase.table("Transaction").select("*").eq("order_id", order_id).execute()
    return data.data


def get_order_with_assigned_address(address: str):
    return (
        supabase.table("File")
        .select("assigned_taproot_address, Order(*)")
        .eq(
            "assigned_taproot_address",
            address,
        )
        .limit(1)
        .execute()
    )


def calculate_fees(size: int, priority_fee: int) -> int:
    base_network_fee = 300
    base_fee = 0.00025 * SATS_PER_BTC
    pct_fee = 0.1
    segwit_size = size / 4
    network_fees = (segwit_size + base_network_fee) * priority_fee
    service_fees = 0 * (network_fees * pct_fee + base_fee)

    return {
        "network_fees": int(network_fees),
        "service_fees": int(service_fees), # Temporarily removing the fees
        "total_fees": int(network_fees + service_fees),
    }


def insert_job(order_id: str):
    result = None
    try:
        result = supabase.table("Job").insert({"order_id": order_id}).execute()
    except postgrest.exceptions.APIError as err:
        print(err)
    return result


def update_job_status(order_id: str, new_status: str):
    return (
        supabase.table("Job")
        .update({"status": new_status})
        .eq("order_id", order_id)
        .execute()
    )


def update_file_status(file_id: str, new_status: str):
    return (
        supabase.table("File")
        .update({"status": new_status})
        .eq("id", file_id)
        .execute()
    )


def get_total_received_sats(order_id):
    data = get_all_txs_from_order_id(order_id)

    total_confirmed_received = sum(
        [item["amount_sats"] if item["confirmations"] > 0 else 0 for item in data]
    )

    total_unconfirmed_received = sum(
        [item["amount_sats"] if item["confirmations"] == 0 else 0 for item in data]
    )

    return {
        "unconfirmed": total_unconfirmed_received,
        "confirmed": total_confirmed_received,
    }


def increase_retry_count(row_id: str):
    try:
        supabase.rpc("increment", {"row_id": row_id}).execute()
    except Exception as e:
        print(
            f"The function might return successfully and the error is a false positive: {e}"
        )
        return None
