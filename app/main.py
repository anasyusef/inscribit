import json
import subprocess
from pprint import pprint

from fastapi import Depends, FastAPI, HTTPException

from .config import COOKIE_PATH, api_key_auth, chain, session
from .constants import Status
from .tasks import inscribe, send_inscription, update_send_status
from .utils import (
    get_inscription_by_commit_tx,
    get_order_with_assigned_address,
    get_status,
    get_total_received_sats,
    get_transaction,
    insert_job,
    parse_transaction_data,
    update_order_status,
    upsert_tx,
)


app = FastAPI()


@app.on_event("startup")
async def startup_event():
    print(f"Using chain: {chain}")
    with open(COOKIE_PATH[chain], "r", encoding="UTF-8") as f:
        auth = f.read().split(":")
        session.auth = tuple(auth)


@app.post("/process/{tx_id}", dependencies=[Depends(api_key_auth)])
async def process(tx_id):
    json_res = get_transaction(tx_id, chain)

    pprint(json_res)
    if json_res.get("error"):
        pprint(json_res.get("error"))
        raise HTTPException(status_code=500)

    parsed_rpc_data = parse_transaction_data(json_res)
    pprint(parsed_rpc_data)

    # address = parsed_rpc_data["details"][0]["address"]
    if not parsed_rpc_data["details"]:
        print(f"{tx_id} looks like a reveal tx")
        return {"detail": "acked!"}

    detail = parsed_rpc_data["details"][0]
    if (
        detail.get("label")
        and "commit" in detail.get("label")
        and detail.get("category") == "send"
    ):
        if parsed_rpc_data["confirmations"] < 1:
            return {"detail": "Waiting for at least 1 confirmation"}
        inscription = get_inscription_by_commit_tx(tx_id)
        print(inscription.data)
        order_id = inscription.data["order_id"]
        update_order_status(order_id, Status.BROADCASTED_CONFIRMED)
        job = send_inscription.delay(inscription.data, chain)
        print(f"Send inscription job id: {job}")
        return parsed_rpc_data

    elif detail["category"] == "send":
        if parsed_rpc_data["confirmations"] < 1:
            return {"detail": "Waiting for at least 1 confirmation"}
        job_id = update_send_status.delay(tx_id)
        print(f"Update status job id: {job_id}")
        return parsed_rpc_data
    order_result = get_order_with_assigned_address(detail["address"])
    pprint(order_result)

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
                job = inscribe.delay(order, chain)
                print(f"Job ID: {job.id}")

    return parsed_rpc_data


@app.get("/wallet", dependencies=[Depends(api_key_auth)])
async def receive():
    """
    Generates wallet receive address
    """
    
    try:
        result = subprocess.run(
            ["ord", f"--chain={chain}", "wallet", "receive"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return json.loads(result.stdout.decode())
    except subprocess.CalledProcessError as err:
        print(err.stderr)
        raise HTTPException(status_code=500) from err


@app.get("/health")
async def health():
    return {"message": "healthy!"}
