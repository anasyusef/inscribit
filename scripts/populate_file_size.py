import dotenv
from supabase.client import create_client, Client
import os

dotenv.load_dotenv()

url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
secret_api_key: str = os.environ.get("SECRET_API_KEY")
supabase: Client = create_client(url, key)

if __name__ == '__main__':
    result = supabase.table("File").select("*, Order(uid)").execute()
    for item in result.data:
        uid = item["Order"]["uid"]
        file_id = item["id"]
        order_id = item["order_id"]
        st = supabase.storage().from_("orders").download(f"{uid}/{order_id}/{file_id}")
        print(supabase.table("File").update({ "size": len(st) }).eq("id", file_id).execute())