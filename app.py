from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import os
import base64
import uuid
import threading
from supabase import create_client, Client
from google import genai

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "gabay-prototype-secret-key")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
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
        print("Gemini: API key not configured.")
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
            if result.data:
                value = result.data[0].get("value")
                if value:
                    return str(value).strip()
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
    return is_logged_in() and session.get("role") in [
        "admin", "government", "gov_employee", "government_employee"
    ]


def is_government_employee():
    return is_logged_in() and session.get("role") in [
        "gov_employee", "government_employee"
    ]


def upload_base64_image(bucket, image_data, extension="jpg"):
    require_supabase()
    if not image_data:
        raise ValueError("No image data provided.")
    if "," in image_data:
        image_data = image_data.split(",", 1)[1]
    try:
        image_bytes = base64.b64decode(image_data, validate=True)
    except Exception as error:
        raise ValueError("Invalid image data.") from error
    if len(image_bytes) > 8 * 1024 * 1024:
        raise ValueError("Image is larger than 8 MB.")
    filename = f"{uuid.uuid4().hex}.{extension}"
    supabase.storage.from_(bucket).upload(
        filename, image_bytes,
        {"content-type": "image/jpeg", "upsert": "false"}
    )
    return filename


def initialize_database():
    print("Checking GABAY configuration...")
    configure_gemini_client(get_configured_gemini_key())
    if not supabase:
        print("Supabase unavailable.")
        return

    default_accounts = [
        {"full_name": "GABAY Administrator", "email": "admin@gabay.gov.ph", "password": "Admin12345!", "role": "admin"},
        {"full_name": "GABAY Government Personnel", "email": "government@gabay.gov.ph", "password": "Gov12345!", "role": "government"},
        {"full_name": "GABAY Government Employee", "email": "employee@gabay.gov.ph", "password": "Employee123!", "role": "gov_employee"}
    ]
    for account in default_accounts:
        try:
            existing = (supabase.table("users").select("id,role")
                        .eq("email", account["email"]).limit(1).execute())
            if not existing.data:
                supabase.table("users").insert({
                    "full_name": account["full_name"], "email": account["email"],
                    "password": account["password"], "role": account["role"],
                    "profile_image": None
                }).execute()
                print("Created default account:", account["email"])
            else:
                print("Account already exists:", account["email"])
        except Exception as error:
            print("Account initialization error:", account["email"], error)


def analyze_request_with_gemini(request_id, category, description):
    try:
        configure_gemini_client(get_configured_gemini_key())
        if not gemini_client:
            print("Gemini unavailable. Using Moderate fallback.")
            supabase.table("requests").update({
                "priority": "Moderate", "analysis_status": "completed"
            }).eq("id", request_id).execute()
            return

        prompt = f"""
You are the emergency severity classification AI for a Philippine government assistance system called GABAY.

Analyze the citizen request. Understand English, Filipino, Tagalog, Bisaya/Cebuano, mixed language, slang, informal grammar, and short messages.

CATEGORY:
{category}

CITIZEN DESCRIPTION:
{description}

CRITICAL: Immediate danger to life, death, serious injury, unconsciousness, drowning, severe bleeding, active fire, building collapse, trapped people, or another emergency requiring immediate response.
HIGH: Serious situation requiring urgent government response but without clear immediate life-threatening danger.
MODERATE: Legitimate government assistance that is not immediately dangerous.
LOW: Routine information, documents, inquiries, or minor non-emergency concerns.

If a person may die or suffer serious harm without immediate action, classify it as CRITICAL.

Return ONLY one:
Critical
High
Moderate
Low

No explanation.
"""
        response = gemini_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = response.text.strip() if response.text else ""
        result = text.lower()
        if "critical" in result:
            priority = "Critical"
        elif "high" in result:
            priority = "High"
        elif "moderate" in result:
            priority = "Moderate"
        elif "low" in result:
            priority = "Low"
        else:
            priority = "Moderate"

        supabase.table("requests").update({
            "priority": priority, "analysis_status": "completed"
        }).eq("id", request_id).execute()
        print("Request:", request_id, "Priority:", priority)
    except Exception as error:
        print("Gemini analysis error:", error)
        try:
            supabase.table("requests").update({
                "priority": "Moderate", "analysis_status": "completed"
            }).eq("id", request_id).execute()
        except Exception as db_error:
            print("Fallback database error:", db_error)


@app.route("/")
def index():
    if is_logged_in():
        if is_government_user():
            return redirect(url_for("government_dashboard"))
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        face_image = request.form.get("face_image", "")
        if not full_name: return "Full name is required.", 400
        if not email: return "Email is required.", 400
        if not password: return "Password is required.", 400
        if not face_image: return "Face verification is required.", 400
        try:
            require_supabase()
            existing = (supabase.table("users").select("id").eq("email", email).limit(1).execute())
            if existing.data: return "Email already registered.", 400
            profile_filename = upload_base64_image(PROFILE_BUCKET, face_image)
            supabase.table("users").insert({
                "full_name": full_name, "email": email, "password": password,
                "role": "citizen", "profile_image": profile_filename
            }).execute()
            return redirect(url_for("login"))
        except Exception as error:
            print("Registration error:", error)
            return "Unable to create account.", 500
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        try:
            require_supabase()
            result = (supabase.table("users").select("*").eq("email", email)
                      .eq("password", password).limit(1).execute())
            user = result.data[0] if result.data else None
        except Exception as error:
            print("Login error:", error)
            return "Unable to connect to database.", 500
        if not user: return "Invalid email or password.", 401
        session["user_id"] = user["id"]
        session["full_name"] = user.get("full_name", "")
        session["role"] = user.get("role", "citizen")
        session["profile_image"] = user.get("profile_image")
        role = session["role"]
        if role == "admin": return redirect(url_for("admin_dashboard"))
        if role in ["government", "gov_employee", "government_employee"]:
            return redirect(url_for("government_dashboard"))
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/dashboard")
def dashboard():
    if not is_logged_in(): return redirect(url_for("login"))
    if is_government_user(): return redirect(url_for("government_dashboard"))
    return render_template("dashboard.html")


@app.route("/government")
@app.route("/government/dashboard")
def government_dashboard():
    if not is_government_user(): return redirect(url_for("login"))
    return render_template("government_dashboard.html")


@app.route("/admin")
@app.route("/admin/dashboard")
def admin_dashboard():
    if not is_admin_user(): return redirect(url_for("login"))
    return render_template("admin_dashboard.html")


@app.route("/api/requests", methods=["POST"])
def create_request():
    if not is_logged_in(): return jsonify({"success": False, "error": "Unauthorized"}), 401
    if is_government_user():
        return jsonify({"success": False, "error": "Government accounts cannot submit citizen requests."}), 403
    require_supabase()
    try:
        data = request.get_json(silent=True) or {}
        category = str(data.get("category", "General Assistance")).strip()
        description = str(data.get("description", "")).strip()
        location = str(data.get("location", "")).strip()
        image_data = data.get("image", "")
        if not description:
            return jsonify({"success": False, "error": "Request description is required."}), 400
        result = supabase.table("requests").insert({
            "user_id": session["user_id"], "category": category, "description": description,
            "location": location, "priority": "Moderate", "analysis_status": "pending",
            "status": "Pending", "assigned_to": None, "forwarded": False
        }).execute()
        if not result.data:
            return jsonify({"success": False, "error": "Unable to create request."}), 500
        request_id = result.data[0]["id"]
        if image_data:
            try:
                filename = upload_base64_image(REPORT_BUCKET, image_data)
                supabase.table("requests").update({"image": filename}).eq("id", request_id).execute()
            except Exception as image_error:
                print("Report image error:", image_error)
        threading.Thread(target=analyze_request_with_gemini,
                         args=(request_id, category, description), daemon=True).start()
        return jsonify({"success": True, "message": "Request submitted successfully.",
                        "request_id": request_id, "priority": "Moderate",
                        "analysis_status": "pending"}), 201
    except Exception as error:
        print("Request creation error:", error)
        return jsonify({"success": False, "error": "Unable to submit your request."}), 500


@app.route("/api/requests", methods=["GET"])
def get_user_requests():
    if not is_logged_in(): return jsonify({"success": False, "error": "Unauthorized"}), 401
    require_supabase()
    try:
        result = (supabase.table("requests").select("*").eq("user_id", session["user_id"])
                  .order("id", desc=True).execute())
        return jsonify({"success": True, "requests": result.data or []})
    except Exception as error:
        print("Get requests error:", error)
        return jsonify({"success": False, "error": "Unable to load requests."}), 500


@app.route("/api/government/requests", methods=["GET"])
def get_government_requests():
    if not is_government_user(): return jsonify({"success": False, "error": "Unauthorized"}), 403
    require_supabase()
    try:
        result = supabase.table("requests").select("*").order("id", desc=True).execute()
        return jsonify({"success": True, "requests": result.data or []})
    except Exception as error:
        print("Government requests error:", error)
        return jsonify({"success": False, "error": "Unable to load government requests."}), 500


@app.route("/api/government/requests/<request_id>", methods=["PATCH"])
def update_government_request(request_id):
    if not is_government_user(): return jsonify({"success": False, "error": "Unauthorized"}), 403
    require_supabase()
    try:
        data = request.get_json(silent=True) or {}
        update_data = {}
        if "status" in data: update_data["status"] = str(data["status"]).strip()
        if "assigned_to" in data: update_data["assigned_to"] = data["assigned_to"]
        if "forwarded" in data: update_data["forwarded"] = bool(data["forwarded"])
        if not update_data: return jsonify({"success": False, "error": "No changes provided."}), 400
        result = supabase.table("requests").update(update_data).eq("id", request_id).execute()
        return jsonify({"success": True, "request": result.data[0] if result.data else None})
    except Exception as error:
        print("Government update error:", error)
        return jsonify({"success": False, "error": "Unable to update request."}), 500


@app.route("/api/government/critical-alerts")
def government_critical_alerts():
    if not is_government_employee():
        return jsonify({"success": False, "error": "Government employee access required.", "alarm_enabled": False}), 403
    require_supabase()
    employee_id = session["user_id"]
    try:
        assigned_result = (supabase.table("requests").select("*").eq("priority", "Critical")
                           .eq("assigned_to", employee_id).eq("forwarded", False)
                           .order("id").limit(1).execute())
        assigned = assigned_result.data[0] if assigned_result.data else None
        if assigned:
            return jsonify({"success": True, "alert": assigned, "alarm_enabled": True})
        candidate_result = (supabase.table("requests").select("*").eq("priority", "Critical")
                            .eq("forwarded", False).is_("assigned_to", "null")
                            .order("id").limit(1).execute())
        candidate = candidate_result.data[0] if candidate_result.data else None
        if candidate:
            supabase.table("requests").update({"assigned_to": employee_id}).eq("id", candidate["id"]).execute()
            candidate["assigned_to"] = employee_id
            print("Critical request assigned to employee:", employee_id)
            return jsonify({"success": True, "alert": candidate, "alarm_enabled": True})
        return jsonify({"success": True, "alert": None, "alarm_enabled": False})
    except Exception as error:
        print("Critical alert error:", error)
        return jsonify({"success": False, "error": "Unable to check critical alerts.", "alarm_enabled": False}), 500


@app.route("/api/admin/gemini-key", methods=["GET"])
def admin_get_gemini_key():
    if not is_admin_user(): return jsonify({"success": False, "error": "Unauthorized"}), 403
    require_supabase()
    try:
        result = (supabase.table("app_settings").select("value").eq("key", "gemini_api_key").limit(1).execute())
        value = result.data[0].get("value", "") if result.data else ""
        return jsonify({"success": True, "configured": bool(value), "key": value})
    except Exception as error:
        print("Admin Gemini key error:", error)
        return jsonify({"success": False, "error": "Unable to load Gemini API key."}), 500


@app.route("/api/admin/gemini-key", methods=["POST"])
def admin_update_gemini_key():
    if not is_admin_user(): return jsonify({"success": False, "error": "Unauthorized"}), 403
    require_supabase()
    try:
        data = request.get_json(silent=True) or {}
        new_key = str(data.get("api_key", "")).strip()
        if not new_key:
            return jsonify({"success": False, "error": "Gemini API key is required."}), 400
        existing = (supabase.table("app_settings").select("id").eq("key", "gemini_api_key").limit(1).execute())
        if existing.data:
            supabase.table("app_settings").update({"value": new_key}).eq("key", "gemini_api_key").execute()
        else:
            supabase.table("app_settings").insert({"key": "gemini_api_key", "value": new_key}).execute()
        configure_gemini_client(new_key)
        return jsonify({"success": True, "message": "Gemini API key updated successfully."})
    except Exception as error:
        print("Admin Gemini key update error:", error)
        return jsonify({"success": False, "error": "Unable to update Gemini API key."}), 500


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


initialize_database()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=False)
