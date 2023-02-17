import subprocess
from celery import Celery
from config import supabase
from utils import calculate_fees

from celery.utils.log import get_task_logger

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
    result = subprocess.run(
        ["ord", f"--chain={chain}", "index"],
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        check=False,
    )

    if result.stdout.decode():
        logger.info(f"stdout: {result.stdout.decode()}")
    if result.stderr.decode():
        logger.error(f"stderr: {result.stderr.decode()}")


@app.task
def inscribe(order):
    order_id = order["id"]
    user_id = order["uid"]
    result = supabase.storage().from_("orders").list(f"{user_id}/{order_id}")
    combined_file_sizes = sum([f["metadata"]["size"] for f in result])
    fees = calculate_fees(combined_file_sizes, order["priority_fee"])
    do_fees_match = fees["total_fees"] == order["payable_amount"]

    if not do_fees_match:
        return "Fees do not match"

    
