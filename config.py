from supabase.client import Client, create_client
from fastapi import FastAPI
import os
import requests
from dotenv import load_dotenv
load_dotenv()

url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

app = FastAPI()

session = requests.Session()
