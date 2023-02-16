import json
import os
import subprocess
from pathlib import Path
from fastapi import HTTPException

from config import app, session
from constants import Status
from utils import (
    get_all_txs_from_order_id,
    get_order_with_assigned_address,
    get_status,
    get_transaction,
    insert_tx,
    parse_transaction_data,
    update_order_status,
)


@app.on_event("startup")
async def startup_event():
    cookie_path = os.path.join(Path.home(), ".bitcoin", ".cookie")
    print("Applying basic auth")
    with open(cookie_path, "r", encoding="UTF-8") as f:
        auth = f.read().split(":")
        session.auth = tuple(auth)


@app.get("/process/{tx_id}")
async def process(tx_id):
    json_res = get_transaction(tx_id)

    if json_res.get("error"):
        raise HTTPException(status_code=500)

    parsed_rpc_data = parse_transaction_data(json_res)

    # "bc1ptljmlzfar6krh98l8j79kwvspx0rj8uhpagf0t9h2hc3w724lyds9kjc7l"
    result_order = get_order_with_assigned_address(parsed_rpc_data["address"])
    print(result_order)

    if result_order.data:
        order = result_order.data[0]
        status = order["status"]
        insert_tx(order["id"], tx_id, parsed_rpc_data)
        data = get_all_txs_from_order_id(order["id"])
        total_received_sats = sum(
            [item["amount_sats"] if item["confirmations"] > 0 else 0 for item in data]
        )

        print(total_received_sats)

        new_status = get_status(
            payable_amount=order["payable_amount"],
            amount_received=total_received_sats,
        )

        if status != new_status:
            update_order_status(order["id"], new_status)

        if new_status in [Status.PAYMENT_RECEIVED, Status.PAYMENT_OVERPAID]:
            # Ensure no duplicates are processed
            print("Push to celery")

    return parsed_rpc_data


@app.get("/wallet")
async def receive():
    """
    Generates wallet receive address
    """
    try:
        result = subprocess.run(
            ["ord", "--chain=signet", "wallet", "receive"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return json.loads(result.stdout.decode())
    except subprocess.CalledProcessError as err:
        print(err.stderr)
        raise HTTPException(status_code=500) from err
