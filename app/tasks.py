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
    update_file_status,
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
def index():
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
def inscribe(self, order_id, chain="mainnet"):
    try:
        files = (
            supabase.table("File")
            .select("*, Order(*)")
            .eq("order_id", order_id)
            .execute()
        )
        # Since all files will have the same settings for now, we can just take the first one and make that the setting for all files
        single_file_result = files.data[0]
        order = single_file_result["Order"]
        user_id = order["uid"]
        priority_fee = single_file_result["priority_fee"]
        update_job_status(order_id, "processing")
        result = supabase.storage().from_("orders").list(f"{user_id}/{order_id}")

        file_sizes = [f["metadata"]["size"] for f in result]
        total_file_fees = 0
        for file_size in file_sizes:
            fee = calculate_fees(file_size, priority_fee)
            total_file_fees += fee["total_fees"]
        do_fees_match = total_file_fees == order["total_payable_amount"]

        if not do_fees_match:
            logger.warn(
                f"Fees do not match - Payable amount: {order['total_payable_amount']} sats - Fees: {total_file_fees} sats"
            )
            return None

        for item in result:
            file_name = item["name"]
            update_file_status(file_name, "processing")
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

            decoded_stdout = result.stdout.decode()

            parsed_result = None

            if decoded_stdout:
                logger.info(decoded_stdout)
                parsed_result = json.loads(decoded_stdout)
                update_file_status(file_name, "inscribing")
                logger.info(f"Moving file to: {file_processed_path}")
                shutil.move(file_path, file_processed_path)
            else:
                logger.error(result.stderr)
                logger.error("Couldn't inscribe. Retrying task...")
                update_file_status(file_name, "failed")
                increase_retry_count(order_id)
                self.retry()

            logger.info(f"Inserting object id: {object_id} and order id: {order_id}")

            supabase.table("File").update(
                {
                    "object_id": object_id,
                    "commit_tx": parsed_result["commit"],
                    "reveal_tx": parsed_result["reveal"],
                    "inscription_id": parsed_result["inscription"],
                    "fees": parsed_result["fees"],
                }
            ).eq("order_id", order_id).eq("id", file_name).execute()
            update_file_status(file_name, Status.BROADCASTED)
        update_job_status(order_id, "completed")

    except MaxRetriesExceededError as e:
        logger.error("Marking the job as failed")
        update_job_status(order_id, "failed")
        raise e


@app.task
def confirm_and_send_inscription(tx_id, chain="mainnet"):
    file_row = (
        supabase.table("File")
        .select("*")
        .eq("commit_tx", tx_id)
        .limit(1)
        .single()
        .execute()
    )
    file_data = file_row.data

    update_file_status(file_data["id"], Status.BROADCASTED_CONFIRMED)

    recipient_address = file_data["recipient_address"]
    inscription_id = file_data["inscription_id"]

    cmd = f"ord --chain={chain} wallet send {recipient_address} {inscription_id} --fee-rate=20"
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
        supabase.table("File").update(
            {"send_tx": result.strip(), "status": Status.INSCRIPTION_SENT}
        ).eq("id", file_data["id"]).execute()
    else:
        logger.error(f"Couldn't send inscription {result.stderr.decode()}")


@app.task(bind=True, default_retry_delay=30, max_retries=3)
def update_send_status(self, tx_id):
    result = (
        supabase.table("File")
        .update({"status": Status.INSCRIPTION_SENT_CONFIRMED})
        .eq("send_tx", tx_id)
        .execute()
    ).data
    print(result)
    # if not inscription_data_list:
    #     print(
    #         f"Couldn't find send_tx with tx id: {tx_id}. Retry count: {update_send_status.request.retries}"
    #     )
    #     self.retry()
    # else:
    #     inscription = inscription_data_list[0]
    #     order_id = inscription["order_id"]
    #     update_order_status(order_id, Status.INSCRIPTION_SENT_CONFIRMED)
