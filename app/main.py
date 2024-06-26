import json
import subprocess

from fastapi import Depends, FastAPI, HTTPException

from .config import (
    COOKIE_PATH,
    api_key_auth,
    chain,
    session,
    supabase,
    logtail_source_token,
)
from .constants import Status
from .tasks import inscribe, confirm_and_send_inscription
from .utils import (
    get_order_with_assigned_address,
    get_status,
    get_total_received_sats,
    get_transaction,
    insert_job,
    parse_transaction_data,
    update_order_status,
    upsert_tx,
)

from logtail import LogtailHandler
import logging

handler = LogtailHandler(source_token=logtail_source_token)


logger = logging.getLogger(__name__)
logger.handlers = []
logger.setLevel(logging.DEBUG)
logger.addHandler(handler)

app = FastAPI()


@app.on_event("startup")
async def startup_event():
    logger.info(f"Using chain: {chain}")
    with open(COOKIE_PATH[chain], "r", encoding="UTF-8") as f:
        auth = f.read().split(":")
        session.auth = tuple(auth)


@app.post("/process/{tx_id}", dependencies=[Depends(api_key_auth)])
async def process(tx_id):
    json_res = get_transaction(tx_id, chain)

    logger.debug(json_res)
    if json_res.get("error"):
        logger.error(json_res.get("error"))
        raise HTTPException(status_code=500)

    parsed_rpc_data = parse_transaction_data(json_res)
    logger.debug(parsed_rpc_data)

    if not parsed_rpc_data["details"]:
        logger.info(f"{tx_id} looks like a reveal tx")
        return {"detail": "acked!"}

    detail = parsed_rpc_data["details"][0]

    if (
        detail.get("label")
        and "commit" in detail.get("label")
        and detail.get("category") == "send"
    ):
        if parsed_rpc_data["confirmations"] < 1:
            return {"detail": "Waiting for at least 1 confirmation"}
        job = confirm_and_send_inscription.delay(tx_id, chain)
        logger.debug(f"Confirm & Send Job id: {job.id}")
        return {
            "type": "confirm_and_send_inscription",
            "job_id": job.id,
            **parsed_rpc_data,
        }

    if detail["category"] == "send":
        if parsed_rpc_data["confirmations"] < 1:
            return {"detail": "Waiting for at least 1 confirmation"}

        result = (
            supabase.table("File")
            .update({"status": Status.INSCRIPTION_SENT_CONFIRMED})
            .eq("send_tx", tx_id)
            .execute()
        )
        logger.debug(result.data)
        return {"type": "update_status", **parsed_rpc_data}

    file_order_result = get_order_with_assigned_address(detail["address"])
    logger.debug(file_order_result)

    if file_order_result.data:
        file_order = file_order_result.data[0]
        order_id = file_order["Order"]["id"]
        status = file_order["Order"]["status"]
        upsert_tx(order_id, tx_id, parsed_rpc_data)
        total_received_sats = get_total_received_sats(order_id)

        logger.info(
            f"Total received from order {order_id}: Unconfirmed: {total_received_sats['unconfirmed']} sats - Confirmed: {total_received_sats['confirmed']}"
        )

        new_status = get_status(
            payable_amount=file_order["Order"]["total_payable_amount"],
            amount_received=total_received_sats,
        )

        if status != new_status:
            result = update_order_status(order_id, new_status)
            logger.info(f"Order ID: {order_id} - Updated")

        logger.info(new_status)
        if new_status in [
            Status.PAYMENT_RECEIVED_CONFIRMED,
            Status.PAYMENT_OVERPAID_CONFIRMED,
        ]:
            result = insert_job(order_id)
            if result:
                job = inscribe.delay(order_id, chain)
                logger.debug(f"Job ID: {job.id}")
            else:
                logger.info(f"Job with assigned order id {order_id} already exists")

    return {**parsed_rpc_data}


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
        logger.error(err.stderr)
        raise HTTPException(status_code=500) from err


@app.get("/health")
async def health():
    return {"message": "healthy!"}
