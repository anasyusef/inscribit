import json
import mimetypes
import os
import subprocess

from celery import Celery
from celery.exceptions import MaxRetriesExceededError
from celery.utils.log import get_task_logger

from .config import chain, supabase
from .constants import FILE_EXTS, PROCESSED_PATH, STORAGE_PATH, Status
from .utils import (
    calculate_fees,
    increase_retry_count,
    update_job_status,
    update_order_status,
)
import shutil

mimetypes.init()

for ext, mime in FILE_EXTS.items():
    mimetypes.add_type(mime, ext)

logger = get_task_logger(__name__)

app = Celery("tasks", backend="redis://", broker="amqp://guest@127.0.0.1//")
app.conf.beat_schedule = {
    "index-every-5-mins": {"task": "tasks.index", "schedule": 600},
}
app.conf.timezone = "UTC"


@app.task
def index(*args):
    logger.info(f"Indexing {chain}")
    cmd = f"ord --chain={chain} index"
    logger.info(cmd)
    result = subprocess.run(
        cmd.split(),
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        check=False,
    )

    if result.stdout.decode():
        logger.info(f"stdout: {result.stdout.decode()}")
    if result.stderr.decode():
        logger.error(f"stderr: {result.stderr.decode()}")


@app.task(bind=True, default_retry_delay=60, max_retries=3)
def inscribe(self, order, chain="mainnet"):
    try:
        order_id = order["id"]
        user_id = order["uid"]
        priority_fee = order["priority_fee"]
        update_job_status(order_id, "processing")
        result = supabase.storage().from_("orders").list(f"{user_id}/{order_id}")
        print(result)
        combined_file_sizes = sum([f["metadata"]["size"] for f in result])
        fees = calculate_fees(combined_file_sizes, order["priority_fee"])
        do_fees_match = fees["total_fees"] == order["payable_amount"]

        if not do_fees_match:
            logger.warn(
                f"Fees do not match - Payable amount: {order['payable_amount']} sats - Fees: {fees['total_fees']} sats"
            )
            return None

        for item in result:
            file_name = item["name"]
            object_id = item["id"]
            result = (
                supabase.storage()
                .from_("orders")
                .download(f"{user_id}/{order_id}/{file_name}")
            )

            file_ext = mimetypes.guess_extension(item["metadata"]["mimetype"])
            file_name_ext = f"{file_name}{file_ext}"
            file_path = os.path.join(STORAGE_PATH, file_name_ext)
            file_processed_path = os.path.join(PROCESSED_PATH, file_name_ext)
            logger.info(f"Writing file: {file_path}")
            with open(file_path, "wb") as f:
                f.write(result)

            cmd = f"ord --chain={chain} wallet inscribe {file_path} --fee-rate {priority_fee}"
            logger.info(cmd)
            result = subprocess.run(
                cmd.split(),
                stderr=subprocess.PIPE,
                stdout=subprocess.PIPE,
                check=False,
            )

            logger.info(f"Moving file to: {file_processed_path}")
            shutil.move(file_path, file_processed_path)
            decoded_stdout = result.stdout.decode()
            print(result.stdout)
            print(result.stderr)
            logger.info(decoded_stdout)
            parsed_result = None
            if decoded_stdout:
                parsed_result = json.loads(decoded_stdout)
            else:
                logger.error("Couldn't inscribe. Retrying task...")
                increase_retry_count(order_id)
                self.retry()

            logger.info(f"Inserting object id: {object_id} and order id: {order_id}")
            supabase.table("inscription").insert(
                {
                    "object_id": object_id,
                    "order_id": order_id,
                    "commit": parsed_result["commit"],
                    "reveal": parsed_result["reveal"],
                    "inscription": parsed_result["inscription"],
                    "fees": parsed_result["fees"],
                }
            ).execute()
        update_job_status(order_id, "completed")
        update_order_status(order_id, Status.BROADCASTED)
        
    except MaxRetriesExceededError as e:
        logger.error("Marking the job as failed")
        update_job_status(order_id, "failed")
        raise e


@app.task
def send_inscription(inscription_data, chain="mainnet"):
    order_id = inscription_data["order_id"]
    order = (
        supabase.table("order")
        .select("recipient_address")
        .eq("id", order_id)
        .limit(1)
        .single()
        .execute()
    )
    recipient_address = order.data["recipient_address"]
    inscription = inscription_data["inscription"]
    cmd = f"ord --chain={chain} wallet send {recipient_address} {inscription} --fee-rate=20"
    logger.info(cmd)
    result = subprocess.run(
        cmd.split(),
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        check=False,
    )

    result = result.stdout.decode()
    if result:
        logger.info(f"Inscription sent: {result}")
        supabase.table("inscription").update({"send_tx": result.strip()}).eq(
            "order_id", order_id
        ).execute()
        update_order_status(order_id, Status.INSCRIPTION_SENT)
    else:
        logger.error(f"Couldn't send inscription {result.stderr.decode()}")


@app.task(bind=True, default_retry_delay=30, max_retries=3)
def update_send_status(self, tx_id):
    inscription_data_list = (
        supabase.table("inscription")
        .select("order_id")
        .eq("send_tx", tx_id)
        .limit(1)
        .execute()
    ).data
    if not inscription_data_list:
        print(
            f"Couldn't find send_tx with tx id: {tx_id}. Retry count: {update_send_status.request.retries}"
        )
        self.retry()
    else:
        inscription = inscription_data_list[0]
        order_id = inscription["order_id"]
        update_order_status(order_id, Status.INSCRIPTION_SENT_CONFIRMED)
