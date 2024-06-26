import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from supabase.client import Client, create_client

from .constants import Chain

load_dotenv()

chain: Chain = Chain(os.environ.get("CHAIN"))
print(f"Loaded with chain: {chain}")
session = requests.Session()

url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
secret_api_key: str = os.environ.get("SECRET_API_KEY")
logtail_source_token: str = os.environ.get("LOGTAIL_SOURCE_TOKEN")
supabase: Client = create_client(url, key)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")  # use token authentication

PORTS = {Chain.MAINNET: 8332, Chain.SIGNET: 38332}

COOKIE_PATH = {
    Chain.MAINNET: os.path.join(Path.home(), ".bitcoin", ".cookie"),
    Chain.SIGNET: os.path.join(Path.home(), ".bitcoin", Chain.SIGNET.value, ".cookie"),
}


def api_key_auth(api_key: str = Depends(oauth2_scheme)):
    if api_key != secret_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Forbidden"
        )
