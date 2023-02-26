import json
import mimetypes
import os
import subprocess
import requests

from celery import Celery
from celery.exceptions import MaxRetriesExceededError

from .config import chain, supabase, logtail_source_token
from .constants import FILE_EXTS, PROCESSED_PATH, STORAGE_PATH, Status
from .utils import (
    # calculate_fees,
    increase_retry_count,
    update_file_status,
    update_job_status,
)
import shutil
from logtail import LogtailHandler
import logging

mimetypes.init()

for ext, mime in FILE_EXTS.items():
    mimetypes.add_type(mime, ext)

handler = LogtailHandler(source_token=logtail_source_token)


logger = logging.getLogger(__name__)
logger.handlers = []
logger.setLevel(logging.DEBUG)
logger.addHandler(handler)

app = Celery("tasks", backend="redis://", broker="amqp://guest@127.0.0.1//")
app.conf.beat_schedule = {
    "index-every-3-mins": {"task": "app.tasks.index", "schedule": 180},
}
app.conf.timezone = "UTC"


@app.task
def index():
    logger.info(f"Indexing {chain}")
    cmd = f"ord --chain={chain} index"
    logger.debug(cmd)
    result = subprocess.run(
        cmd.split(),
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        check=False,
    )

    logger.info(f"stdout: {result.stdout.decode()}")
    logger.error(f"stderr: {result.stderr.decode()}")


@app.task(bind=True, default_retry_delay=90, max_retries=3)
def do_inscribe(self, file_, order_id, user_id, chain="mainnet"):
    file_id = file_["id"]
    priority_fee = file_["priority_fee"]
    try:
        update_file_status(file_id, "inscribing")
        result = (
            supabase.storage()
            .from_("orders")
            .download(f"{user_id}/{order_id}/{file_id}")
        )

        file_size = len(result)

        if file_["size"] != len(result):
            raise RuntimeError(
                f"Size of the file on db != actual size: {file_['size']} bytes - Fees: {file_size} bytes"
            )

        file_ext = mimetypes.guess_extension(file_["mime_type"])
        file_name_ext = f"{file_id}{file_ext}"
        file_path = os.path.join(STORAGE_PATH, file_name_ext)
        file_processed_path = os.path.join(PROCESSED_PATH, file_name_ext)
        logger.info(f"Writing file: {file_path}")

        with open(file_path, "wb") as f:
            f.write(result)

        cmd = (
            f"ord --chain={chain} wallet inscribe {file_path} --fee-rate {priority_fee}"
        )
        logger.debug(cmd)
        result = subprocess.run(
            cmd.split(),
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            check=True,
        )

        decoded_stdout = result.stdout.decode()

        logger.info(decoded_stdout)
        parsed_result = json.loads(decoded_stdout)
        logger.info(f"Moving file to: {file_processed_path}")
        shutil.move(file_path, file_processed_path)

        logger.info(f"Inserting file: {file_id} with order id: {order_id}")

        supabase.table("File").update(
            {
                "commit_tx": parsed_result["commit"],
                "reveal_tx": parsed_result["reveal"],
                "inscription_id": parsed_result["inscription"],
                "fees": parsed_result["fees"],
            }
        ).eq("order_id", order_id).eq("id", file_id).execute()
        update_file_status(file_id, Status.BROADCASTED)
    except Exception as e:
        logger.error(e)
        logger.error(e.stderr.decode())
        logger.error(f"Couldn't inscribe file {file_id}")
        update_file_status(file_id, "failed_to_inscribe")


@app.task(bind=True, default_retry_delay=60, max_retries=3)
def inscribe(self, order_id, chain="mainnet"):
    try:
        files = (
            supabase.table("File")
            .select("*, Order(uid)")
            .eq("order_id", order_id)
            .eq("status", "pending")
            .execute()
        )
        if len(files.data) == 0:
            logger.warn(f"Couldn't find any files with pending status and order id: {order_id}")
            return
        # Since all files will have the same settings for now, we can just take the first one and make that the setting for all files
        single_file_result = files.data[0]
        order = single_file_result["Order"]
        user_id = order["uid"]
        update_job_status(order_id, "processing")
        for item in files.data:
            do_inscribe.delay(item, order_id, user_id, chain)
        update_job_status(order_id, "completed")

    except MaxRetriesExceededError as e:
        logger.error("Marking the job as failed")
        update_job_status(order_id, "failed")
        raise e
    except Exception as e:
        self.retry(exc=e)  # Does it introduce infinite retries?


@app.task(bind=True, default_retry_delay=90, max_retries=3)
def confirm_and_send_inscription(self, tx_id, chain="mainnet"):
    logger.debug(f"Commit tx: {tx_id.lower()}")
    file_row = (
        supabase.table("File")
        .select("*")
        .eq("commit_tx", tx_id.lower())
        .eq("status", Status.BROADCASTED)
        .limit(1)
        .execute()
    )

    file_data_list = file_row.data
    if not file_data_list:
        msg = f"Couldn't find a file with commit tx: {tx_id}"
        logger.warn(msg)
        return msg
    file_data = file_data_list[0]

    recipient_address = file_data["recipient_address"]
    inscription_id = file_data["inscription_id"]
    try:
        cmd = f"ord --chain={chain} wallet inscriptions"
        logger.debug(cmd)
        result = subprocess.run(
            cmd.split(),
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            check=True,
        )
        decoded_result = json.loads(result.stdout.decode())
        filtered_inscriptions_list = list(
            filter(lambda x: x["inscription"] == inscription_id, decoded_result)
        )
        if len(filtered_inscriptions_list) == 0:
            logger.warn(
                f"Couldn't inscription with inscription id: {inscription_id}. Perhaps we need to wait for another confirmation or wait to sync the wallet"
            )
            return None

    except subprocess.CalledProcessError as e:
        logger.error(e)
        logger.error(e.stderr.decode())
        self.retry(exc=e)

    update_file_status(file_data["id"], Status.BROADCASTED_CONFIRMED)
    try:
        fee_rate = 20
        res = requests.get("https://mempool.space/api/v1/fees/recommended")
        if res.ok:
            fee_rate = res.json()["fastestFee"]
        cmd = f"ord --chain={chain} wallet send {recipient_address} {inscription_id} --fee-rate={fee_rate}"
        logger.debug(cmd)
        result = subprocess.run(
            cmd.split(),
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            check=True,
        )

        decoded_result = result.stdout.decode()
        logger.info(f"Inscription sent: {decoded_result}")
        supabase.table("File").update(
            {"send_tx": decoded_result.strip(), "status": Status.INSCRIPTION_SENT}
        ).eq("id", file_data["id"]).execute()
    except Exception as e:
        logger.error(e)
        logger.error(e.stderr.decode())
        logger.error(f"Couldn't send inscription with file id: {file_data['id']}")
        update_file_status(file_data["id"], "failed_to_send")
