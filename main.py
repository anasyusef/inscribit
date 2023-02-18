import json
import subprocess

from fastapi import HTTPException

from config import COOKIE_PATH, app, session
from constants import Chain, Status
from tasks import inscribe
from utils import (
    get_all_txs_from_order_id,
    get_order_with_assigned_address,
    get_status,
    get_total_received_sats,
    get_transaction,
    upsert_tx,
    parse_transaction_data,
    update_order_status,
    insert_job,
)


@app.on_event("startup")
async def startup_event():
    chain = Chain.SIGNET
    print(f"Using chain: {chain}")
    with open(COOKIE_PATH[chain], "r", encoding="UTF-8") as f:
        auth = f.read().split(":")
        session.auth = tuple(auth)


@app.post("/process/{tx_id}")
async def process(tx_id, chain: Chain = Chain.MAINNET):
    json_res = get_transaction(tx_id, chain)

    if json_res.get("error"):
        print(json_res.get("error"))
        raise HTTPException(status_code=500)

    parsed_rpc_data = parse_transaction_data(json_res)
    print(parsed_rpc_data)

    order_result = get_order_with_assigned_address(parsed_rpc_data["address"])
    print(order_result)

    if order_result.data:
        order = order_result.data[0]
        status = order["status"]
        upsert_tx(order["id"], tx_id, parsed_rpc_data)
        total_received_sats = get_total_received_sats(order["id"])

        print(
            f"Total received from order {order['id']}: Unconfirmed: {total_received_sats['unconfirmed']} sats - Confirmed: {total_received_sats['confirmed']}"
        )

        new_status = get_status(
            payable_amount=order["payable_amount"],
            amount_received=total_received_sats,
        )

        if status != new_status:
            print(f"Order ID:{order['id']} - Updated")
            result = update_order_status(order["id"], new_status)
            order = result.data[0]

        if new_status == Status.PAYMENT_RECEIVED_CONFIRMED:
            result = insert_job(order["id"])
            if result:
                job = inscribe.delay(order)
                print(f"Job ID: {job.id}")

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
