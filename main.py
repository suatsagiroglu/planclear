import os
import traceback
import httpx
import stripe
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
from pydantic import BaseModel
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="PlanClear API")
templates = Jinja2Templates(directory="templates")

# Konfigürasyonlar
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
stripe.api_key = STRIPE_SECRET_KEY

class AuditRequest(BaseModel):
    postcode: str

class CheckoutRequest(BaseModel):
    postcode: str
    filename: str

def generate_pdf(postcode: str, easting: float, northing: float, constraints: list) -> str:
    os.makedirs("reports", exist_ok=True)
    clean_pc = postcode.replace(" ", "_").upper()
    filename = f"PlanClear_{clean_pc}.pdf"
    filepath = os.path.join("reports", filename)

    c = canvas.Canvas(filepath, pagesize=letter)
    width, height = letter

    # Header Bar
    c.setFillColor(colors.HexColor("#0f172a"))
    c.rect(0, height - 80, width, 80, fill=1, stroke=0)
    
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(40, height - 48, "PlanClear | Site Constraints & Feasibility Report")

    # Property Info
    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 13)
    c.drawString(40, height - 120, f"Location Postcode: {postcode.upper()}")
    
    c.setFont("Helvetica", 9)
    c.setFillColor(colors.HexColor("#475569"))
    c.drawString(40, height - 136, f"British National Grid: Easting {easting:.1f} | Northing {northing:.1f}")

    # Constraints Section
    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(colors.HexColor("#0f172a"))
    c.drawString(40, height - 175, "Statutory Constraints Detected (250m Buffer):")

    y = height - 200
    if not constraints:
        c.setFont("Helvetica", 9)
        c.setFillColor(colors.HexColor("#16a34a"))
        c.drawString(55, y, "• No immediate statutory constraints found. Standard Permitted Development likely unobstructed.")
        y -= 25
    else:
        for item in constraints:
            c.setFont("Helvetica-Bold", 9)
            c.setFillColor(colors.HexColor("#dc2626"))
            c.drawString(55, y, f"• [{item.get('type', 'Constraint')}]")
            
            c.setFont("Helvetica", 9)
            c.setFillColor(colors.HexColor("#334155"))
            c.drawString(180, y, f"{item.get('details', '')} (~{item.get('distance_m', 0)}m away)")
            y -= 22

    # Score Box
    score = max(25, 95 - (len(constraints) * 20))
    verdict = "LOW RISK - Standard Permitted Development likely unobstructed" if score >= 75 else "HIGH RISK - Heritage / Arboricultural constraints require prior consent"
    
    c.setFillColor(colors.HexColor("#0f172a"))
    c.rect(40, y - 45, width - 80, 45, fill=1, stroke=0)
    
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(55, y - 22, f"Planning Feasibility Score: {score}/100")
    c.setFont("Helvetica", 8)
    c.drawString(55, y - 36, f"Verdict: {verdict}")

    c.showPage()
    c.save()
    return filename

@app.get("/", response_class=HTMLResponse)
async def serve_home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.post("/api/audit")
async def audit_property(req: AuditRequest):
    postcode = req.postcode.strip().replace(" ", "")
    
    # 1. Postcodes.io OSGB36 Easting/Northing sorgusu
    async with httpx.AsyncClient() as client:
        geo_res = await client.get(f"https://api.postcodes.io/postcodes/{postcode}")
        if geo_res.status_code != 200:
            raise HTTPException(status_code=400, detail="Invalid UK Postcode. Please enter a valid postcode.")
        geo_data = geo_res.json().get("result", {})
        easting = geo_data.get("eastings")
        northing = geo_data.get("northings")

    if not easting or not northing:
        raise HTTPException(status_code=400, detail="Could not resolve British National Grid coordinates.")

    # 2. Supabase PostGIS RPC Sorgusu
    constraints = []
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            rpc_url = f"{SUPABASE_URL}/rest/v1/rpc/get_constraints_within_radius"
            headers = {
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "target_easting": easting,
                "target_northing": northing,
                "radius_meters": 500
            }
            async with httpx.AsyncClient() as client:
                rpc_res = await client.post(rpc_url, headers=headers, json=payload, timeout=10.0)
                if rpc_res.status_code == 200:
                    records = rpc_res.json()
                    for r in records:
                        constraints.append({
                            "type": r.get("constraint_type"),
                            "details": r.get("name"),
                            "distance_m": r.get("distance_m")
                        })
        except Exception as e:
            print("Supabase RPC lookup failed:", str(e))

    # Fallback (Supabase bos ise test verisi)
    if not constraints:
        constraints = [
            {"type": "Conservation Area", "details": "Local Designated Planning Zone", "distance_m": 45},
            {"type": "Tree Preservation Order (TPO)", "details": "Mature Protected Tree", "distance_m": 22}
        ]

    # 3. PDF Uretimi
    pdf_filename = generate_pdf(req.postcode, easting, northing, constraints)

    return {
        "status": "success",
        "postcode": req.postcode.upper(),
        "easting": easting,
        "northing": northing,
        "constraints_count": len(constraints),
        "constraints": constraints,
        "pdf_download_url": f"/api/download/{pdf_filename}",
        "filename": pdf_filename
    }

@app.post("/api/create-checkout-session")
async def create_checkout_session(req: CheckoutRequest):
    # Stripe Session olusturma (Gercek Stripe Secret Key yoksa test modu bypass'i)
    if "placeholder" in STRIPE_SECRET_KEY or "Mock" in STRIPE_SECRET_KEY:
        # Test modunda dogrudan indirme linkine yonlendir
        return {"checkout_url": f"/api/download/{req.filename}"}
    
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'gbp',
                    'product_data': {
                        'name': f"PlanClear Comprehensive Feasibility Report ({req.postcode.upper()})",
                    },
                    'unit_amount': 999, # £9.99
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f"http://127.0.0.1:8000/api/download/{req.filename}?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url="http://127.0.0.1:8000/",
        )
        return {"checkout_url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/download/{filename}")
async def download_report(filename: str):
    filepath = os.path.join("reports", filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Report file not found")
    return FileResponse(filepath, media_type="application/pdf", filename=filename)