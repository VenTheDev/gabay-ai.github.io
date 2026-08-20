# GABAY updated app.py
# Fixes:
# 1. Prevents duplicated /rest/v1 in SUPABASE_URL.
# 2. Passes full_name/profile_image/role to government and admin dashboards.

from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import os, base64, uuid, threading
from supabase import create_client, Client
from google import genai

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "gabay-prototype-secret-key")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_URL = SUPABASE_URL.replace("/rest/v1", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
supabase: Client | None = None

if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        print("Supabase: CONNECTED")
    except Exception as error:
        print("Supabase connection error:", error)
else:
    print("WARNING: Supabase environment variables are not configured.")

PROFILE_BUCKET = "profiles"
REPORT_BUCKET = "reports"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
gemini_client = None

def configure_gemini_client(api_key):
    global GEMINI_API_KEY, gemini_client
    GEMINI_API_KEY = (api_key or "").strip()
    if not GEMINI_API_KEY:
        gemini_client = None
        return
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        print("Gemini: CONNECTED")
    except Exception as error:
        gemini_client = None
        print("Gemini configuration error:", error)

def get_configured_gemini_key():
    if supabase:
        try:
            result = (supabase.table("app_settings").select("value")
                      .eq("key", "gemini_api_key").limit(1).execute())
            if result.data and result.data[0].get("value"):
                return str(result.data[0]["value"]).strip()
        except Exception as error:
            print("Gemini settings load error:", error)
    return os.environ.get("GEMINI_API_KEY", "").strip()

def require_supabase():
    if not supabase:
        raise RuntimeError("Supabase is not configured.")

def is_logged_in():
    return "user_id" in session

def is_admin_user():
    return is_logged_in() and session.get("role") == "admin"

def is_government_user():
    return is_logged_in() and session.get("role") in ["admin","government","gov_employee","government_employee"]

def is_government_employee():
    return is_logged_in() and session.get("role") in ["gov_employee","government_employee"]

def upload_base64_image(bucket, image_data, extension="jpg"):
    require_supabase()
    if "," in image_data:
        image_data = image_data.split(",", 1)[1]
    image_bytes = base64.b64decode(image_data, validate=True)
    if len(image_bytes) > 8 * 1024 * 1024:
        raise ValueError("Image is larger than 8 MB.")
    filename = f"{uuid.uuid4().hex}.{extension}"
    supabase.storage.from_(bucket).upload(filename, image_bytes, {"content-type":"image/jpeg","upsert":"false"})
    return filename

def initialize_database():
    print("Checking GABAY configuration...")
    configure_gemini_client(get_configured_gemini_key())
    if not supabase:
        return
    default_accounts = [
        {"full_name":"GABAY Administrator","email":"admin@gabay.gov.ph","password":"Admin12345!","role":"admin"},
        {"full_name":"GABAY Government Personnel","email":"government@gabay.gov.ph","password":"Gov12345!","role":"government"},
        {"full_name":"GABAY Government Employee","email":"employee@gabay.gov.ph","password":"Employee123!","role":"gov_employee"}
    ]
    for account in default_accounts:
        try:
            existing = supabase.table("users").select("id,role").eq("email",account["email"]).limit(1).execute()
            if not existing.data:
                supabase.table("users").insert({
                    "full_name":account["full_name"],"email":account["email"],
                    "password":account["password"],"role":account["role"],"profile_image":None
                }).execute()
                print("Created default account:", account["email"])
        except Exception as error:
            print("Account initialization error:", account["email"], error)

@app.route("/")
def index():
    if is_logged_in():
        return redirect(url_for("government_dashboard" if is_government_user() else "dashboard"))
    return render_template("index.html")

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        full_name=request.form.get("full_name","").strip()
        email=request.form.get("email","").strip().lower()
        password=request.form.get("password","")
        face_image=request.form.get("face_image","")
        if not full_name: return "Full name is required.",400
        if not email: return "Email is required.",400
        if not password: return "Password is required.",400
        if not face_image: return "Face verification is required.",400
        try:
            require_supabase()
            if supabase.table("users").select("id").eq("email",email).limit(1).execute().data:
                return "Email already registered.",400
            profile_filename=upload_base64_image(PROFILE_BUCKET,face_image)
            supabase.table("users").insert({
                "full_name":full_name,"email":email,"password":password,
                "role":"citizen","profile_image":profile_filename
            }).execute()
            return redirect(url_for("login"))
        except Exception as error:
            print("Registration error:",error)
            return "Unable to create account.",500
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email=request.form.get("email","").strip().lower()
        password=request.form.get("password","")
        if not email or not password:
            return "Email and password are required.",400
        try:
            require_supabase()
            # FIX: this uses the Supabase client, which correctly targets /rest/v1/users.
            result=(supabase.table("users").select("*")
                    .eq("email",email).eq("password",password).limit(1).execute())
            user=result.data[0] if result.data else None
        except Exception as error:
            print("Login error:",error)
            return "Unable to connect to database.",500
        if not user:
            return "Invalid email or password.",401
        session["user_id"]=user["id"]
        session["full_name"]=user.get("full_name") or "GABAY User"
        session["role"]=user.get("role") or "citizen"
        session["profile_image"]=user.get("profile_image")
        if session["role"]=="admin":
            return redirect(url_for("admin_dashboard"))
        if session["role"] in ["government","gov_employee","government_employee"]:
            return redirect(url_for("government_dashboard"))
        return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    if not is_logged_in(): return redirect(url_for("login"))
    if is_government_user(): return redirect(url_for("government_dashboard"))
    return render_template("dashboard.html",
        full_name=session.get("full_name","GABAY User"),
        profile_image=session.get("profile_image"))

@app.route("/government")
@app.route("/government/dashboard")
def government_dashboard():
    if not is_government_user(): return redirect(url_for("login"))
    # FIX: government_dashboard.html expects full_name.
    return render_template("government_dashboard.html",
        full_name=session.get("full_name","GABAY Government Personnel"),
        profile_image=session.get("profile_image"),
        role=session.get("role",""))

@app.route("/admin")
@app.route("/admin/dashboard")
def admin_dashboard():
    if not is_admin_user(): return redirect(url_for("login"))
    return render_template("admin_dashboard.html",
        full_name=session.get("full_name","GABAY Administrator"),
        profile_image=session.get("profile_image"),
        role=session.get("role","admin"))

# Keep the remaining API routes from your existing app.py below this point.
# They can remain unchanged because the login/Supabase URL and dashboard fixes
# above address the errors shown in your Vercel logs.

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

initialize_database()

if __name__ == "__main__":
    port=int(os.environ.get("PORT",3000))
    app.run(host="0.0.0.0",port=port,debug=False)
