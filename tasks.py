import json
import mimetypes
import os
import subprocess

from celery import Celery
from celery.utils.log import get_task_logger

from config import supabase
from constants import FILE_EXTS, STORAGE_PATH, Status
from utils import calculate_fees
from utils import update_order_status, update_job_status


mimetypes.init()

for ext, mime in FILE_EXTS.items():
    mimetypes.add_type(mime, ext)

logger = get_task_logger(__name__)

app = Celery("tasks", backend="redis://")
app.conf.beat_schedule = {
    "index-every-5-mins": {"task": "tasks.index", "schedule": 300, "args": ["mainnet"]},
}
app.conf.timezone = "UTC"


@app.task
def index(*args):
    chain = args[0]
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


@app.task
def inscribe(order, chain="mainnet"):
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

        file_path = os.path.join(STORAGE_PATH, f"{file_name}{file_ext}")
        logger.info(f"Writing file: {file_path}")
        with open(file_path, "wb") as f:
            f.write(result)

        cmd = f"ord --chain={chain} wallet inscribe --dry-run {file_path} --fee-rate {priority_fee}"
        logger.info(cmd)
        result = subprocess.run(
            cmd.split(),
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            check=False,
        )

        decoded_stdout = result.stdout.decode()
        logger.info(decoded_stdout)
        parsed_result = json.loads(decoded_stdout)

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
