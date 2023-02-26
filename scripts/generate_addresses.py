import subprocess
import json
import os

CHAIN = "mainnet"
FILE_NAME = "addresses.csv"
AMOUNT_TO_SEND = 6000
LABEL = "0"
NUM_ADDRESSES = 30

cmd = f"ord --chain={CHAIN} wallet receive"


with open(os.path.join(os.path.dirname(__file__), FILE_NAME), "w") as f:
    for _ in range(NUM_ADDRESSES):
        result = subprocess.run(cmd.split(), stdout=subprocess.PIPE)
        address = json.loads(result.stdout.decode())["address"]
        f.write(f"{address},{AMOUNT_TO_SEND},{LABEL}\n")
