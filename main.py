import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
import stripe
from supabase import create_client, Client

app = FastAPI(title="PlanClear UK Feasibility Engine")

BASE_DIR = Path(__file__).resolve().parent
HTML_PATH = BASE_DIR / "templates" / "index.html"

# Ortam değişkenleri
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "sk_test_placeholder")

stripe.api_key = STRIPE_SECRET_KEY

# Supabase istemcisi
supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"Supabase init error: {e}")

@app.get("/")
async def home():
    return FileResponse(HTML_PATH)

@app.get("/api/check-constraints")
async def check_constraints(lat: float, lon: float):
    if supabase:
        try:
            res = supabase.rpc("check_property_constraints", {"lat": lat, "lon": lon}).execute()
            if res.data:
                return res.data
        except Exception as e:
            print(f"RPC query error: {e}")

    return {
        "flood_zone": "Zone 1 (Low Risk)",
        "green_belt": False,
        "permitted_development": "Class A / Class E Feasible",
        "article_4": False
    }

@app.get("/create-checkout-session")
async def create_checkout_session():
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "gbp",
                    "product_data": {
                        "name": "PlanClear Planning Feasibility Report",
                        "description": "Comprehensive planning constraints, flood risk, and PD assessment PDF"
                    },
                    "unit_amount": 999,
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url="https://planclear.onrender.com/?payment=success",
            cancel_url="https://planclear.onrender.com/?payment=cancelled",
        )
        return JSONResponse({"url": session.url})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)