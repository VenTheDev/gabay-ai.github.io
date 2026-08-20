from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import os
import base64
import uuid
import re
from datetime import datetime
from supabase import create_client, Client
from google import genai

# =========================================================
# GABAY AI - VERCEL / SUPABASE VERSION
# =========================================================
# This version fixes the Vercel/Supabase problems shown in the logs:
# 1. Removes duplicated /rest/v1 from SUPABASE_URL.
# 2. Login queries by email first, so passwords are NOT placed in the
#    Supabase REST URL/logs.
# 3. Uses Supabase for users, requests, and app_settings.
# 4. Keeps admin Gemini-key management.
# 5. Critical alarms are ONLY available to government employees.
# 6. Keeps citizen request, government dashboard, AI report, and
#    forwarding endpoints.
#
# Required Vercel environment variables:
# SUPABASE_URL=https://YOUR_PROJECT.supabase.co
# SUPABASE_SERVICE_ROLE_KEY=YOUR_SERVICE_ROLE_KEY
# FLASK_SECRET_KEY=long-random-secret
# GEMINI_API_KEY=optional-initial-key
# GEMINI_MODEL=gemini-3.6-flash (or your available model)
# =========================================================

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "gabay-prototype-secret-key")

# -----------------------------
# Supabase
# -----------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_URL = re.sub(r"/rest/v1/?$", "", SUPABASE_URL).rstrip("/")
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

# -----------------------------
# Gemini
# -----------------------------
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
gemini_client = None

PROFILE_BUCKET = "profiles"
REPORT_BUCKET = "reports"
VERIFICATION_BUCKET = "verification"

GOVERNMENT_DEPARTMENTS = {
    "Philippine National Police": {
        "phone": "PROTOTYPE-PNP-NUMBER",
        "email": "pnp@gabay-prototype.gov.ph",
    },
    "Bureau of Fire Protection": {
        "phone": "PROTOTYPE-BFP-NUMBER",
        "email": "bfp@gabay-prototype.gov.ph",
    },
    "Local Disaster Risk Reduction and Management Office": {
        "phone": "PROTOTYPE-LDRRMO-NUMBER",
        "email": "ldrrmo@gabay-prototype.gov.ph",
    },
    "City/Municipal Health Office": {
        "phone": "PROTOTYPE-HEALTH-NUMBER",
        "email": "health@gabay-prototype.gov.ph",
    },
    "Social Welfare and Development Office": {
        "phone": "PROTOTYPE-CSWDO-NUMBER",
        "email": "socialwelfare@gabay-prototype.gov.ph",
    },
    "Engineering and Public Works Office": {
        "phone": "PROTOTYPE-ENGINEERING-NUMBER",
        "email": "engineering@gabay-prototype.gov.ph",
    },
    "Electric Power / Energy Office": {
        "phone": "PROTOTYPE-ENERGY-NUMBER",
        "email": "energy@gabay-prototype.gov.ph",
    },
}


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
    """Supabase app_settings wins; environment variable is fallback."""
    if supabase:
        try:
            result = (
                supabase.table("app_settings")
                .select("value")
                .eq("key", "gemini_api_key")
                .limit(1)
                .execute()
            )
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
    return is_logged_in() and session.get("role") in {
        "admin",
        "government",
        "gov_employee",
        "government_employee",
    }


def is_government_employee():
    # IMPORTANT: alarms are employee-only.
    return is_logged_in() and session.get("role") in {
        "gov_employee",
        "government_employee",
    }


def public_storage_url(bucket, filename):
    if not filename or not supabase:
        return None
    try:
        return supabase.storage.from_(bucket).get_public_url(filename)
    except Exception:
        return None


def upload_base64_image(bucket, image_data, extension="jpg"):
    require_supabase()

    if not image_data:
        raise ValueError("No image supplied.")

    if "," in image_data:
        image_data = image_data.split(",", 1)[1]

    image_bytes = base64.b64decode(image_data, validate=True)

    if len(image_bytes) > 8 * 1024 * 1024:
        raise ValueError("Image is larger than 8 MB.")

    filename = f"{uuid.uuid4().hex}.{extension}"

    supabase.storage.from_(bucket).upload(
        filename,
        image_bytes,
        {
            "content-type": "image/jpeg",
            "upsert": "false",
        },
    )

    return filename


def row_to_dict(row):
    if row is None:
        return None
    return dict(row)


def get_user_by_id(user_id):
    require_supabase()
    result = (
        supabase.table("users")
        .select("*")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def get_request_by_id(request_id):
    require_supabase()
    result = (
        supabase.table("requests")
        .select("*")
        .eq("id", request_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# =========================================================
# DEFAULT ACCOUNT INITIALIZATION
# =========================================================
# Existing rows are NEVER overwritten. This is important because
# your Supabase admin row already exists.
# =========================================================

def initialize_database():
    print("Checking GABAY configuration...")
    configure_gemini_client(get_configured_gemini_key())

    if not supabase:
        return

    # Only create accounts if they do not already exist.
    # Change these in Supabase if your project uses different credentials.
    default_accounts = [
        {
            "full_name": "GABAY Administrator",
            "email": "admin@gabay.gov.ph",
            "password": "Admin12345!",
            "role": "admin",
        },
        {
            "full_name": "GABAY Government Personnel",
            "email": "government@gabay.gov.ph",
            "password": "Gov12345!",
            "role": "government",
        },
        {
            "full_name": "GABAY Government Employee",
            "email": "employee@gabay.gov.ph",
            "password": "Employee123!",
            "role": "gov_employee",
        },
    ]

    for account in default_accounts:
        try:
            existing = (
                supabase.table("users")
                .select("id,role")
                .eq("email", account["email"])
                .limit(1)
                .execute()
            )

            if not existing.data:
                supabase.table("users").insert(
                    {
                        "full_name": account["full_name"],
                        "email": account["email"],
                        "password": account["password"],
                        "role": account["role"],
                        "profile_image": None,
                    }
                ).execute()

                print("Created default account:", account["email"])
        except Exception as error:
            print("Account initialization error:", account["email"], error)


# =========================================================
# AUTHENTICATION
# =========================================================

@app.route("/")
def index():
    if is_logged_in():
        if is_admin_user() or is_government_user():
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

        if not full_name:
            return "Full name is required.", 400
        if not email:
            return "Email is required.", 400
        if not password:
            return "Password is required.", 400
        if not face_image:
            return "Face verification is required.", 400

        try:
            require_supabase()

            existing = (
                supabase.table("users")
                .select("id")
                .eq("email", email)
                .limit(1)
                .execute()
            )

            if existing.data:
                return "Email already registered.", 400

            profile_filename = upload_base64_image(
                PROFILE_BUCKET,
                face_image,
            )

            supabase.table("users").insert(
                {
                    "full_name": full_name,
                    "email": email,
                    "password": password,
                    "role": "citizen",
                    "profile_image": profile_filename,
                }
            ).execute()

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

        if not email or not password:
            return "Email and password are required.", 400

        try:
            require_supabase()

            # SECURITY/LOG FIX:
            # Do NOT filter by password in the Supabase REST request.
            # The old code generated URLs containing the plaintext password.
            result = (
                supabase.table("users")
                .select("*")
                .eq("email", email)
                .limit(1)
                .execute()
            )

            user = result.data[0] if result.data else None

        except Exception as error:
            print("Login database error:", error)
            return "Unable to connect to database.", 500

        if not user:
            return "Invalid email or password.", 401

        # This matches the current database design where the project stores
        # a password column. For production, migrate to Supabase Auth/hashed
        # passwords instead of plaintext passwords.
        stored_password = str(user.get("password") or "")

        if stored_password != password:
            return "Invalid email or password.", 401

        session.clear()
        session["user_id"] = user["id"]
        session["full_name"] = user.get("full_name") or "GABAY User"
        session["role"] = user.get("role") or "citizen"
        session["profile_image"] = user.get("profile_image")

        if session["role"] == "admin":
            return redirect(url_for("admin_dashboard"))

        if session["role"] in {
            "government",
            "gov_employee",
            "government_employee",
        }:
            return redirect(url_for("government_dashboard"))

        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# =========================================================
# DASHBOARDS
# =========================================================

@app.route("/dashboard")
def dashboard():
    if not is_logged_in():
        return redirect(url_for("login"))

    if is_government_user():
        return redirect(url_for("government_dashboard"))

    return render_template(
        "dashboard.html",
        full_name=session.get("full_name", "GABAY User"),
        role=session.get("role", "citizen"),
        profile_image=public_storage_url(
            PROFILE_BUCKET,
            session.get("profile_image"),
        ),
    )


@app.route("/admin")
@app.route("/admin/dashboard")
def admin_dashboard():
    if not is_admin_user():
        return redirect(url_for("login"))

    return render_template(
        "admin_dashboard.html",
        full_name=session.get("full_name", "GABAY Administrator"),
        role="admin",
        profile_image=public_storage_url(
            PROFILE_BUCKET,
            session.get("profile_image"),
        ),
    )


@app.route("/government")
@app.route("/government/dashboard")
def government_dashboard():
    if not is_government_user():
        return redirect(url_for("login"))

    try:
        require_supabase()

        total = (
            supabase.table("requests")
            .select("id", count="exact")
            .execute()
        )
        critical = (
            supabase.table("requests")
            .select("id", count="exact")
            .eq("priority", "Critical")
            .execute()
        )
        high = (
            supabase.table("requests")
            .select("id", count="exact")
            .eq("priority", "High")
            .execute()
        )
        moderate = (
            supabase.table("requests")
            .select("id", count="exact")
            .eq("priority", "Moderate")
            .execute()
        )
        low = (
            supabase.table("requests")
            .select("id", count="exact")
            .eq("priority", "Low")
            .execute()
        )
        pending = (
            supabase.table("requests")
            .select("id", count="exact")
            .eq("status", "Pending")
            .execute()
        )
        in_progress = (
            supabase.table("requests")
            .select("id", count="exact")
            .eq("status", "In Progress")
            .execute()
        )
        resolved = (
            supabase.table("requests")
            .select("id", count="exact")
            .eq("status", "Resolved")
            .execute()
        )

        recent_result = (
            supabase.table("requests")
            .select("*, users(full_name)")
            .order("id", desc=True)
            .limit(20)
            .execute()
        )

        recent_requests = recent_result.data or []

    except Exception as error:
        print("Government dashboard error:", error)
        total = critical = high = moderate = low = pending = in_progress = resolved = None
        recent_requests = []

    return render_template(
        "government_dashboard.html",
        full_name=session.get("full_name", "GABAY Government Personnel"),
        role=session.get("role", ""),
        profile_image=public_storage_url(
            PROFILE_BUCKET,
            session.get("profile_image"),
        ),
        total_requests=getattr(total, "count", 0) or 0,
        critical_requests=getattr(critical, "count", 0) or 0,
        high_requests=getattr(high, "count", 0) or 0,
        moderate_requests=getattr(moderate, "count", 0) or 0,
        low_requests=getattr(low, "count", 0) or 0,
        pending_requests=getattr(pending, "count", 0) or 0,
        in_progress_requests=getattr(in_progress, "count", 0) or 0,
        resolved_requests=getattr(resolved, "count", 0) or 0,
        recent_requests=recent_requests,
    )


# =========================================================
# CITIZEN REQUESTS
# =========================================================

@app.route("/request/<category>")
def request_page(category):
    if not is_logged_in():
        return redirect(url_for("login"))

    if is_government_user():
        return redirect(url_for("government_dashboard"))

    return render_template(
        "request.html",
        category=category,
        full_name=session.get("full_name", "GABAY User"),
        profile_image=public_storage_url(
            PROFILE_BUCKET,
            session.get("profile_image"),
        ),
    )


def fallback_priority(category, description):
    text = f"{category} {description}".lower()

    critical_terms = [
        "not breathing",
        "no response",
        "unconscious",
        "heart attack",
        "drowning",
        "drowned",
        "severe bleeding",
        "bleeding heavily",
        "fire",
        "house fire",
        "building collapse",
        "trapped",
        "electrocuted",
        "electrocution",
        "multiple people",
        "life threatening",
        "life-threatening",
        "di na humihinga",
        "walang malay",
        "walang response",
        "atake sa puso",
        "nalulunod",
        "nalunod",
        "malakas na pagdurugo",
        "sunog",
        "nakulong",
        "nakakulong",
    ]

    high_terms = [
        "injured",
        "accident",
        "danger",
        "flood",
        "evacuation",
        "threat",
        "power outage",
        "blackout",
        "electrical line",
        "gas leak",
        "serious",
        "emergency",
        "baha",
        "aksidente",
        "nasugatan",
        "panganib",
        "brownout",
        "putol na kuryente",
        "tagas ng gas",
    ]

    low_terms = [
        "information",
        "inquiry",
        "document",
        "certificate",
        "how to",
        "schedule",
        "requirements",
        "impormasyon",
        "tanong",
        "dokumento",
        "requirements",
    ]

    if any(term in text for term in critical_terms):
        return "Critical"

    if any(term in text for term in high_terms):
        return "High"

    if any(term in text for term in low_terms):
        return "Low"

    return "Moderate"


def classify_priority_with_ai(category, description):
    configure_gemini_client(get_configured_gemini_key())

    fallback = fallback_priority(category, description)

    if not gemini_client or not GEMINI_API_KEY:
        return fallback, "fallback"

    prompt = f"""
You are GABAY AI, an emergency severity classifier for a Philippine
government assistance system.

Understand English, Filipino/Tagalog, Cebuano/Bisaya, mixed language,
slang, spelling mistakes, and informal writing.

CATEGORY:
{category}

CITIZEN DESCRIPTION:
{description}

Classify the request into exactly ONE level:

Critical = immediate danger to life, death, serious injury, active fire,
drowning, unconscious/not breathing person, trapped people, building
collapse, or another situation requiring immediate emergency response.

High = serious and urgent but no clear immediate life-threatening danger.

Moderate = legitimate assistance that needs action but can wait.

Low = routine/non-urgent inquiry, information, documents, or minor issue.

If a person may die or suffer serious harm without immediate action,
choose Critical.

RETURN ONLY:
Critical
High
Moderate
Low
"""

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )

        answer = (response.text or "").strip().lower()

        if "critical" in answer:
            return "Critical", "gemini"
        if "high" in answer:
            return "High", "gemini"
        if "moderate" in answer:
            return "Moderate", "gemini"
        if "low" in answer:
            return "Low", "gemini"

    except Exception as error:
        print("Gemini priority error:", error)

    return fallback, "fallback"


def recommend_department(category, description):
    text = f"{category} {description}".lower()

    if any(x in text for x in ["fire", "sunog", "smoke"]):
        return "Bureau of Fire Protection"

    if any(
        x in text
        for x in [
            "police",
            "robbery",
            "threat",
            "crime",
            "assault",
            "suspect",
            "holdap",
        ]
    ):
        return "Philippine National Police"

    if any(
        x in text
        for x in [
            "heart attack",
            "unconscious",
            "not breathing",
            "drowning",
            "injury",
            "medical",
            "ambulance",
            "ospital",
            "doctor",
            "nasugatan",
            "nalunod",
        ]
    ):
        return "City/Municipal Health Office"

    if any(
        x in text
        for x in [
            "power",
            "electric",
            "electricity",
            "brownout",
            "blackout",
            "kuryente",
            "poste",
            "power line",
        ]
    ):
        return "Electric Power / Energy Office"

    if any(
        x in text
        for x in [
            "road",
            "bridge",
            "building",
            "pothole",
            "drainage",
            "infrastructure",
        ]
    ):
        return "Engineering and Public Works Office"

    if any(
        x in text
        for x in ["flood", "baha", "evacuation", "landslide", "disaster"]
    ):
        return "Local Disaster Risk Reduction and Management Office"

    if any(
        x in text
        for x in ["food", "cash", "shelter", "social", "family assistance"]
    ):
        return "Social Welfare and Development Office"

    return "Local Disaster Risk Reduction and Management Office"


@app.route("/submit-request", methods=["POST"])
def submit_request():
    if not is_logged_in():
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    if is_government_user():
        return jsonify(
            {"success": False, "error": "Government accounts cannot submit citizen requests."}
        ), 403

    category = request.form.get("category", "").strip()
    description = request.form.get("description", "").strip()
    contact_number = request.form.get("contact_number", "").strip()
    email = request.form.get("email", "").strip()
    address = request.form.get("address", "").strip()
    latitude = request.form.get("latitude", "").strip()
    longitude = request.form.get("longitude", "").strip()

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        category = str(payload.get("category", category)).strip()
        description = str(payload.get("description", description)).strip()
        contact_number = str(payload.get("contact_number", contact_number)).strip()
        email = str(payload.get("email", email)).strip()
        address = str(payload.get("address", address)).strip()
        latitude = str(payload.get("latitude", latitude)).strip()
        longitude = str(payload.get("longitude", longitude)).strip()

    if not category:
        return jsonify({"success": False, "error": "Category is required."}), 400
    if not description:
        return jsonify({"success": False, "error": "Description is required."}), 400
    if not contact_number:
        return jsonify({"success": False, "error": "Contact number is required."}), 400
    if not address:
        return jsonify({"success": False, "error": "Address is required."}), 400

    try:
        require_supabase()

        verification_image = None

        if request.files.get("verification_image"):
            file = request.files["verification_image"]
            raw = file.read()

            if len(raw) > 8 * 1024 * 1024:
                return jsonify(
                    {"success": False, "error": "Verification image is larger than 8 MB."}
                ), 400

            filename = f"{uuid.uuid4().hex}.jpg"

            supabase.storage.from_(VERIFICATION_BUCKET).upload(
                filename,
                raw,
                {
                    "content-type": file.mimetype or "image/jpeg",
                    "upsert": "false",
                },
            )

            verification_image = filename

        elif request.form.get("verification_image"):
            verification_image = upload_base64_image(
                VERIFICATION_BUCKET,
                request.form.get("verification_image"),
            )

        priority, analysis_source = classify_priority_with_ai(
            category,
            description,
        )

        ticket_number = "GABAY-" + datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6].upper()
        created_at = datetime.now().isoformat(timespec="seconds")
        department = recommend_department(category, description)

        insert_data = {
            "ticket_number": ticket_number,
            "user_id": session["user_id"],
            "category": category,
            "description": description,
            "contact_number": contact_number,
            "email": email or None,
            "address": address,
            "latitude": latitude or None,
            "longitude": longitude or None,
            "priority": priority,
            "status": "Pending",
            "assigned_department": department,
            "verification_image": verification_image,
            "created_at": created_at,
            "analysis_status": "completed",
            "forwarded": 0,
            "forward_method": None,
            "forward_target": None,
            "forwarded_by": None,
            "forwarded_at": None,
        }

        try:
            result = supabase.table("requests").insert(insert_data).execute()
        except Exception as error:
            # Some older Supabase schemas do not yet contain the forwarding
            # columns. Retry using only the original request columns.
            print("Extended request insert failed; retrying:", error)

            fallback_data = {
                key: insert_data[key]
                for key in [
                    "ticket_number",
                    "user_id",
                    "category",
                    "description",
                    "contact_number",
                    "email",
                    "address",
                    "latitude",
                    "longitude",
                    "priority",
                    "status",
                    "assigned_department",
                    "verification_image",
                    "created_at",
                    "analysis_status",
                ]
            }

            result = supabase.table("requests").insert(fallback_data).execute()

        created = result.data[0] if result.data else None

        if not created:
            return jsonify(
                {"success": False, "error": "Request was not created."}
            ), 500

        request_id = created["id"]

        # API/AJAX callers receive JSON. Traditional HTML forms are redirected.
        if request.is_json or request.headers.get("Accept", "").find("application/json") >= 0:
            return jsonify(
                {
                    "success": True,
                    "id": request_id,
                    "ticket_number": ticket_number,
                    "priority": priority,
                    "department": department,
                    "analysis_source": analysis_source,
                    "status": "Pending",
                }
            )

        return redirect(
            url_for(
                "request_processing",
                request_id=request_id,
            )
        )

    except Exception as error:
        print("Submit request error:", error)
        return jsonify(
            {"success": False, "error": "Unable to submit emergency request."}
        ), 500


@app.route("/request-processing/<int:request_id>")
def request_processing(request_id):
    if not is_logged_in():
        return redirect(url_for("login"))

    report = get_request_by_id(request_id)

    if not report:
        return "Request not found.", 404

    if report.get("user_id") != session["user_id"] and not is_government_user():
        return "Unauthorized.", 403

    return render_template(
        "request_processing.html",
        request_data=report,
    )


@app.route("/api/request-status/<int:request_id>")
def request_status(request_id):
    if not is_logged_in():
        return jsonify({"error": "Unauthorized"}), 401

    report = get_request_by_id(request_id)

    if not report:
        return jsonify({"error": "Request not found"}), 404

    if report.get("user_id") != session["user_id"] and not is_government_user():
        return jsonify({"error": "Unauthorized"}), 403

    is_ready = (
        report.get("analysis_status") == "completed"
        and report.get("priority") not in {None, "", "Analyzing"}
    )

    return jsonify(
        {
            "ready": is_ready,
            "id": report.get("id"),
            "ticket_number": report.get("ticket_number"),
            "category": report.get("category"),
            "description": report.get("description"),
            "priority": report.get("priority"),
            "status": report.get("status"),
            "analysis_status": report.get("analysis_status"),
            "assigned_department": report.get("assigned_department"),
            "created_at": report.get("created_at"),
        }
    )


@app.route("/request-info/<int:request_id>")
def request_info(request_id):
    if not is_logged_in():
        return redirect(url_for("login"))

    report = get_request_by_id(request_id)

    if not report:
        return "Request not found.", 404

    if report.get("user_id") != session["user_id"] and not is_government_user():
        return "Unauthorized.", 403

    return jsonify(report)


# =========================================================
# GOVERNMENT API
# =========================================================

@app.route("/api/government/departments")
def government_departments():
    if not is_government_user():
        return jsonify({"error": "Unauthorized"}), 403

    departments = [
        {
            "name": name,
            "phone": data["phone"],
            "email": data["email"],
        }
        for name, data in GOVERNMENT_DEPARTMENTS.items()
    ]

    return jsonify(
        {
            "success": True,
            "prototype_mode": True,
            "departments": departments,
        }
    )


@app.route("/api/government/dashboard-updates")
def government_dashboard_updates():
    if not is_government_user():
        return jsonify({"error": "Unauthorized"}), 403

    try:
        require_supabase()

        latest_result = (
            supabase.table("requests")
            .select("*")
            .order("id", desc=True)
            .limit(1)
            .execute()
        )

        latest = latest_result.data[0] if latest_result.data else None

        critical_result = (
            supabase.table("requests")
            .select("*")
            .eq("priority", "Critical")
            .eq("status", "Pending")
            .order("id", desc=False)
            .limit(1)
            .execute()
        )

        active = critical_result.data[0] if critical_result.data else None

        # Admins and government personnel may see dashboard data,
        # but alarms remain employee-only.
        alarm_data = active if is_government_employee() else None

        return jsonify(
            {
                "success": True,
                "latest_request": latest,
                "alarm_active": bool(alarm_data),
                "alarm_request": alarm_data,
            }
        )

    except Exception as error:
        print("Dashboard updates error:", error)
        return jsonify(
            {
                "success": False,
                "error": "Unable to load dashboard updates.",
            }
        ), 500


@app.route("/api/government/critical-alerts")
def government_critical_alerts():
    # ADMIN MUST NEVER receive an alarm.
    if session.get("role") == "admin":
        return jsonify(
            {
                "success": True,
                "alert": None,
                "alarm_enabled": False,
            }
        )

    # GOVERNMENT PERSONNEL ACCOUNT ALSO DOES NOT RECEIVE THE ALARM.
    if not is_government_employee():
        if not is_logged_in():
            return jsonify({"error": "Unauthorized"}), 401
        return jsonify({"error": "Unauthorized"}), 403

    try:
        require_supabase()

        employee_id = session["user_id"]

        # First look for an emergency already assigned to this employee.
        assigned_result = (
            supabase.table("requests")
            .select("*")
            .eq("priority", "Critical")
            .eq("assigned_to", employee_id)
            .eq("forwarded", 0)
            .order("id")
            .limit(1)
            .execute()
        )

        assigned = assigned_result.data[0] if assigned_result.data else None

        # If none is assigned, claim the oldest unassigned critical request.
        if not assigned:
            candidates = (
                supabase.table("requests")
                .select("*")
                .eq("priority", "Critical")
                .eq("forwarded", 0)
                .is_("assigned_to", "null")
                .order("id")
                .limit(1)
                .execute()
            )

            candidate = candidates.data[0] if candidates.data else None

            if candidate:
                try:
                    updated = (
                        supabase.table("requests")
                        .update({"assigned_to": employee_id})
                        .eq("id", candidate["id"])
                        .is_("assigned_to", "null")
                        .eq("forwarded", 0)
                        .execute()
                    )

                    assigned = updated.data[0] if updated.data else None
                except Exception as error:
                    # Older schemas may not have assigned_to.
                    print("Critical assignment error:", error)
                    assigned = candidate

        if not assigned:
            return jsonify(
                {
                    "success": True,
                    "alert": None,
                    "alarm_enabled": True,
                }
            )

        return jsonify(
            {
                "success": True,
                "alarm_enabled": True,
                "alert": {
                    "id": assigned.get("id"),
                    "ticket_number": assigned.get("ticket_number"),
                    "category": assigned.get("category"),
                    "description": assigned.get("description"),
                    "priority": assigned.get("priority"),
                    "status": assigned.get("status"),
                    "created_at": assigned.get("created_at"),
                    "address": assigned.get("address"),
                    "latitude": assigned.get("latitude"),
                    "longitude": assigned.get("longitude"),
                    "assigned_department": assigned.get("assigned_department"),
                    "ai_report": assigned.get("ai_report"),
                    "map_screenshot": assigned.get("map_screenshot"),
                },
            }
        )

    except Exception as error:
        print("Critical alert error:", error)
        return jsonify(
            {
                "success": False,
                "alarm_enabled": True,
                "error": "Unable to check critical alerts.",
            }
        ), 500


# =========================================================
# AI WRITTEN EMERGENCY REPORT
# =========================================================

def generate_ai_emergency_report(report):
    prompt = f"""
You are GABAY AI, an emergency coordination assistant for a Philippine
government assistance system.

Write a concise, professional incident report for the assigned department.

Use ONLY the supplied information. Do not invent facts. If information
is missing, write "Not provided".

Ticket Number: {report.get("ticket_number")}
Citizen: {report.get("full_name") or report.get("user_id")}
Category: {report.get("category")}
Priority: {report.get("priority")}
Status: {report.get("status")}
Date Submitted: {report.get("created_at")}
Address: {report.get("address")}
Latitude: {report.get("latitude")}
Longitude: {report.get("longitude")}

Citizen Description:
{report.get("description")}

Use these headings:

GABAY EMERGENCY INCIDENT REPORT
Incident Summary
Location
Reported Situation
Priority Assessment
Recommended Immediate Action
Information for Responding Department

Keep it factual and concise.
"""

    configure_gemini_client(get_configured_gemini_key())

    if gemini_client and GEMINI_API_KEY:
        try:
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
            )

            text = (response.text or "").strip()

            if text:
                return text

        except Exception as error:
            print("AI report generation error:", error)

    return f"""GABAY EMERGENCY INCIDENT REPORT

Incident Summary
Ticket Number: {report.get("ticket_number")}
Citizen: {report.get("full_name") or "Not provided"}
Category: {report.get("category")}
Date Submitted: {report.get("created_at")}

Location
Address: {report.get("address") or "Not provided"}
Latitude: {report.get("latitude") or "Not provided"}
Longitude: {report.get("longitude") or "Not provided"}

Reported Situation
{report.get("description") or "Not provided"}

Priority Assessment
{report.get("priority") or "Not provided"}

Recommended Immediate Action
The assigned government department should verify the reported situation
and dispatch the appropriate response according to its procedures.

Information for Responding Department
This report was generated from a citizen-submitted GABAY emergency request.
"""


@app.route("/api/government/ai-report/<int:request_id>")
def government_ai_report(request_id):
    if not is_government_user():
        return jsonify(
            {"success": False, "error": "Unauthorized"}
        ), 403

    try:
        report = get_request_by_id(request_id)

        if not report:
            return jsonify(
                {"success": False, "error": "Report not found."}
            ), 404

        if report.get("priority") != "Critical":
            return jsonify(
                {
                    "success": False,
                    "error": "Only Critical reports receive an emergency report.",
                }
            ), 400

        if report.get("ai_report"):
            return jsonify(
                {
                    "success": True,
                    "report": report["ai_report"],
                }
            )

        user = get_user_by_id(report.get("user_id"))
        report["full_name"] = user.get("full_name") if user else "Not provided"

        ai_report = generate_ai_emergency_report(report)

        supabase.table("requests").update(
            {"ai_report": ai_report}
        ).eq("id", request_id).execute()

        return jsonify(
            {
                "success": True,
                "report": ai_report,
            }
        )

    except Exception as error:
        print("AI report endpoint error:", error)
        return jsonify(
            {
                "success": False,
                "error": "Unable to generate AI report.",
            }
        ), 500


# =========================================================
# FORWARD REQUEST
# =========================================================
# Prototype only: NO actual SMS or email is sent.
# =========================================================

@app.route(
    "/api/government/forward-request/<int:request_id>",
    methods=["POST"],
)
def forward_request(request_id):
    if not is_government_user():
        if not is_logged_in():
            return jsonify(
                {"success": False, "error": "Unauthorized"}
            ), 401

        return jsonify(
            {"success": False, "error": "Unauthorized"}
        ), 403

    try:
        report = get_request_by_id(request_id)

        if not report:
            return jsonify(
                {"success": False, "error": "Request not found."}
            ), 404

        if report.get("forwarded") in (1, True):
            return jsonify(
                {
                    "success": False,
                    "error": "This emergency was already handled.",
                }
            ), 409

        payload = request.get_json(silent=True) or {}

        department = (
            payload.get("department")
            or report.get("assigned_department")
            or recommend_department(
                report.get("category", ""),
                report.get("description", ""),
            )
        )

        method_name = str(payload.get("method") or "Prototype forwarding")
        target = str(
            payload.get("target")
            or GOVERNMENT_DEPARTMENTS.get(
                department,
                {},
            ).get("email", "Prototype target")
        )

        forwarded_at = datetime.now().isoformat(timespec="seconds")

        ai_report = report.get("ai_report")

        if report.get("priority") == "Critical" and not ai_report:
            user = get_user_by_id(report.get("user_id"))
            report["full_name"] = user.get("full_name") if user else "Not provided"
            ai_report = generate_ai_emergency_report(report)

        update_data = {
            "forwarded": 1,
            "forward_method": method_name,
            "forward_target": target,
            "forwarded_by": session["user_id"],
            "forwarded_at": forwarded_at,
            "assigned_department": department,
            "status": "In Progress",
        }

        if ai_report:
            update_data["ai_report"] = ai_report

        try:
            result = (
                supabase.table("requests")
                .update(update_data)
                .eq("id", request_id)
                .eq("forwarded", 0)
                .execute()
            )
        except Exception as error:
            print("Extended forward update failed:", error)

            # Compatibility with older requests schemas.
            result = (
                supabase.table("requests")
                .update(
                    {
                        "assigned_department": department,
                        "status": "In Progress",
                    }
                )
                .eq("id", request_id)
                .execute()
            )

        if not result.data:
            return jsonify(
                {
                    "success": False,
                    "error": "This emergency was already handled by another employee.",
                }
            ), 409

        return jsonify(
            {
                "success": True,
                "prototype_mode": True,
                "message": f"This report has been forwarded to {department}.",
                "ticket_number": report.get("ticket_number"),
                "department": department,
                "method": method_name,
                "target": target,
                "forwarded_at": forwarded_at,
                "alarm_should_stop": True,
                "ai_report": ai_report,
                "report_submitted": True,
                "note": "NO ACTUAL SMS OR EMAIL WAS SENT.",
            }
        )

    except Exception as error:
        print("Forward request error:", error)
        return jsonify(
            {
                "success": False,
                "error": "Unable to forward request.",
            }
        ), 500


# =========================================================
# ADMIN GEMINI SETTINGS
# =========================================================

@app.route("/api/admin/gemini-settings", methods=["GET"])
def admin_gemini_settings():
    if not is_admin_user():
        return jsonify(
            {
                "success": False,
                "error": "Administrator access required.",
            }
        ), 403

    key = get_configured_gemini_key()

    return jsonify(
        {
            "success": True,
            "configured": bool(key),
            "masked_key": (
                "••••••••" + key[-4:]
                if len(key) >= 4
                else ("••••••••" if key else "")
            ),
            "model": GEMINI_MODEL,
        }
    )


@app.route("/api/admin/gemini-settings", methods=["POST"])
def update_admin_gemini_settings():
    if not is_admin_user():
        return jsonify(
            {
                "success": False,
                "error": "Administrator access required.",
            }
        ), 403

    payload = request.get_json(silent=True) or {}
    api_key = str(payload.get("api_key", "")).strip()

    if not api_key:
        return jsonify(
            {
                "success": False,
                "error": "Enter a Gemini API key.",
            }
        ), 400

    try:
        require_supabase()

        # Upsert in Supabase. This allows the admin to change the key
        # without rebuilding/redeploying the APK/site.
        supabase.table("app_settings").upsert(
            {
                "key": "gemini_api_key",
                "value": api_key,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            on_conflict="key",
        ).execute()

        configure_gemini_client(api_key)

        return jsonify(
            {
                "success": True,
                "message": "Gemini API key updated successfully.",
                "configured": True,
                "masked_key": "••••••••" + api_key[-4:],
            }
        )

    except Exception as error:
        print("Gemini settings update error:", error)
        return jsonify(
            {
                "success": False,
                "error": "Unable to save Gemini API key.",
            }
        ), 500


@app.route("/api/admin/gemini-settings/clear", methods=["POST"])
def clear_admin_gemini_settings():
    if not is_admin_user():
        return jsonify(
            {
                "success": False,
                "error": "Administrator access required.",
            }
        ), 403

    try:
        require_supabase()

        supabase.table("app_settings").delete().eq(
            "key", "gemini_api_key"
        ).execute()

        configure_gemini_client("")

        return jsonify(
            {
                "success": True,
                "message": "Gemini API key removed from GABAY.",
            }
        )

    except Exception as error:
        print("Gemini settings clear error:", error)
        return jsonify(
            {
                "success": False,
                "error": "Unable to remove Gemini API key.",
            }
        ), 500


# =========================================================
# STORAGE ROUTES
# =========================================================
# These routes are retained for compatibility with existing HTML.
# If your Supabase buckets are public, the returned URLs can also be
# used directly by the frontend.
# =========================================================

@app.route("/uploads/profiles/<path:filename>")
def profile_upload(filename):
    url = public_storage_url(PROFILE_BUCKET, filename)

    if not url:
        return "", 404

    return redirect(url)


@app.route("/uploads/verification/<path:filename>")
def verification_upload(filename):
    url = public_storage_url(VERIFICATION_BUCKET, filename)

    if not url:
        return "", 404

    return redirect(url)


# =========================================================
# STARTUP
# =========================================================

initialize_database()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "3000"))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
    )
