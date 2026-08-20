from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify
)

import os
import base64
import uuid
import threading

from datetime import datetime
from supabase import create_client, Client
from google import genai


# =========================================================
# GABAY APPLICATION
# =========================================================

app = Flask(__name__)

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "gabay-prototype-secret-key"
)


# =========================================================
# SUPABASE CONFIGURATION
# =========================================================

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    ""
).strip()

SUPABASE_SERVICE_ROLE_KEY = os.environ.get(
    "SUPABASE_SERVICE_ROLE_KEY",
    ""
).strip()


supabase: Client | None = None


if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:

    try:

        supabase = create_client(
            SUPABASE_URL,
            SUPABASE_SERVICE_ROLE_KEY
        )

        print("Supabase: CONNECTED")

    except Exception as error:

        print(
            "Supabase connection error:",
            error
        )

else:

    print(
        "WARNING: Supabase environment variables "
        "are not configured."
    )


# =========================================================
# STORAGE BUCKETS
# =========================================================

PROFILE_BUCKET = "profiles"
VERIFICATION_BUCKET = "verification"
REPORT_BUCKET = "reports"
MAP_BUCKET = "maps"


# =========================================================
# GEMINI
# =========================================================

GEMINI_API_KEY = os.environ.get(
    "GEMINI_API_KEY",
    ""
).strip()

gemini_client = None

GEMINI_MODEL = "gemini-3.6-flash"


def configure_gemini_client(api_key):

    global GEMINI_API_KEY
    global gemini_client

    GEMINI_API_KEY = (
        api_key or ""
    ).strip()

    if GEMINI_API_KEY:

        try:

            gemini_client = genai.Client(
                api_key=GEMINI_API_KEY
            )

        except Exception as error:

            print(
                "Gemini configuration error:",
                error
            )

            gemini_client = None

    else:

        gemini_client = None


def get_configured_gemini_key():

    if not supabase:

        return (
            os.environ.get(
                "GEMINI_API_KEY",
                ""
            )
            .strip()
        )


    try:

        result = (
            supabase
            .table("app_settings")
            .select("value")
            .eq(
                "key",
                "gemini_api_key"
            )
            .limit(1)
            .execute()
        )


        if result.data:

            value = result.data[0].get(
                "value"
            )

            if value:

                return str(
                    value
                ).strip()


    except Exception as error:

        print(
            "Gemini settings load error:",
            error
        )


    return (
        os.environ.get(
            "GEMINI_API_KEY",
            ""
        )
        .strip()
    )


# =========================================================
# GOVERNMENT DEPARTMENTS
# =========================================================

GOVERNMENT_DEPARTMENTS = {

    "Philippine National Police": {
        "phone": "PROTOTYPE-PNP-NUMBER",
        "email": "pnp@gabay-prototype.gov.ph"
    },

    "Bureau of Fire Protection": {
        "phone": "PROTOTYPE-BFP-NUMBER",
        "email": "bfp@gabay-prototype.gov.ph"
    },

    "Local Disaster Risk Reduction and Management Office": {
        "phone": "PROTOTYPE-LDRRMO-NUMBER",
        "email": "ldrrmo@gabay-prototype.gov.ph"
    },

    "City/Municipal Health Office": {
        "phone": "PROTOTYPE-HEALTH-NUMBER",
        "email": "health@gabay-prototype.gov.ph"
    },

    "Social Welfare and Development Office": {
        "phone": "PROTOTYPE-DSWD-NUMBER",
        "email": "socialwelfare@gabay-prototype.gov.ph"
    },

    "Public Works and Engineering Office": {
        "phone": "PROTOTYPE-ENGINEERING-NUMBER",
        "email": "engineering@gabay-prototype.gov.ph"
    }

}


# =========================================================
# SUPABASE CHECK
# =========================================================

def require_supabase():

    if not supabase:

        raise RuntimeError(
            "Supabase is not configured."
        )


# =========================================================
# GOVERNMENT ROLE
# =========================================================

def is_government_user():

    return (
        "user_id" in session
        and session.get("role") in [
            "admin",
            "government",
            "gov_employee",
            "government_employee"
        ]
    )


def is_admin_user():

    return (
        "user_id" in session
        and session.get("role") == "admin"
    )


# =========================================================
# STORAGE
# =========================================================

def upload_base64_image(
    bucket,
    image_data,
    extension="jpg"
):

    require_supabase()


    if not image_data:

        raise ValueError(
            "No image data provided."
        )


    if "," in image_data:

        image_data = image_data.split(
            ",",
            1
        )[1]


    try:

        image_bytes = base64.b64decode(
            image_data,
            validate=True
        )

    except Exception as error:

        raise ValueError(
            "Invalid image data."
        ) from error


    if len(image_bytes) > 8 * 1024 * 1024:

        raise ValueError(
            "Image is larger than 8 MB."
        )


    filename = (
        f"{uuid.uuid4().hex}.{extension}"
    )


    supabase.storage.from_(
        bucket
    ).upload(
        filename,
        image_bytes,
        {
            "content-type": "image/jpeg",
            "upsert": "false"
        }
    )


    return filename


def upload_text_file(
    bucket,
    filename,
    text
):

    require_supabase()


    data = text.encode(
        "utf-8"
    )


    supabase.storage.from_(
        bucket
    ).upload(
        filename,
        data,
        {
            "content-type": "text/plain",
            "upsert": "true"
        }
    )


    return filename


# =========================================================
# INITIALIZE
# =========================================================

def initialize_database():

    print(
        "Checking GABAY Supabase configuration..."
    )


    configure_gemini_client(
        get_configured_gemini_key()
    )


    # -----------------------------------------------------
    # Default government account
    # -----------------------------------------------------

    if not supabase:

        print(
            "Supabase unavailable. "
            "Default account cannot be checked."
        )

        return


    try:

        result = (
            supabase
            .table("users")
            .select("id")
            .eq(
                "email",
                "government@gabay.gov.ph"
            )
            .limit(1)
            .execute()
        )


        if not result.data:

            supabase.table(
                "users"
            ).insert({
                "full_name":
                    "GABAY Government Personnel",

                "email":
                    "government@gabay.gov.ph",

                "password":
                    "Gov12345!",

                "role":
                    "government",

                "profile_image":
                    None

            }).execute()


            print(
                "Default government account created."
            )


    except Exception as error:

        print(
            "Government account initialization error:",
            error
        )


# =========================================================
# GEMINI SEVERITY ANALYSIS
# =========================================================

def analyze_request_with_gemini(
    request_id,
    category,
    description
):

    configure_gemini_client(
        get_configured_gemini_key()
    )


    print()
    print(
        "========================================"
    )
    print(
        "GABAY GEMINI BACKGROUND ANALYSIS"
    )
    print(
        "Request ID:",
        request_id
    )
    print(
        "Category:",
        category
    )
    print(
        "Description:",
        description
    )


    try:

        if not GEMINI_API_KEY or not gemini_client:

            print(
                "Gemini AI: API key not configured."
            )


            supabase.table(
                "requests"
            ).update({

                "priority":
                    "Moderate",

                "analysis_status":
                    "completed"

            }).eq(
                "id",
                request_id
            ).execute()


            return


        prompt = f"""

You are the emergency severity classification AI
for a Philippine government assistance system called GABAY.

Analyze the citizen request.

Understand English, Filipino, Tagalog, Bisaya/Cebuano,
mixed language, slang, informal grammar and short messages.

CATEGORY:

{category}

CITIZEN DESCRIPTION:

{description}

CLASSIFICATION:

CRITICAL:
Immediate danger to life, death, serious injury,
unconsciousness, drowning, severe bleeding, active fire,
building collapse, trapped people, or another emergency
requiring immediate response.

HIGH:
Serious situation requiring urgent government response
but without clear immediate life-threatening danger.

MODERATE:
Legitimate government assistance that is not immediately
dangerous.

LOW:
Routine information, documents, inquiries, or minor
non-emergency concerns.

If a person may die or suffer serious harm without
immediate action, classify it as CRITICAL.

Return ONLY:

Critical
High
Moderate
Low

No explanation.
"""


        response = (
            gemini_client
            .models
            .generate_content(
                model=GEMINI_MODEL,
                contents=prompt
            )
        )


        severity_response = (
            response.text.strip()
            if response.text
            else ""
        )


        severity_lower = (
            severity_response
            .lower()
            .strip()
        )


        if "critical" in severity_lower:

            priority = "Critical"

        elif "high" in severity_lower:

            priority = "High"

        elif "moderate" in severity_lower:

            priority = "Moderate"

        elif "low" in severity_lower:

            priority = "Low"

        else:

            priority = "Moderate"


        supabase.table(
            "requests"
        ).update({

            "priority":
                priority,

            "analysis_status":
                "completed"

        }).eq(
            "id",
            request_id
        ).execute()


        print(
            "FINAL PRIORITY:",
            priority
        )


    except Exception as error:

        print(
            "Gemini severity analysis error:",
            error
        )


        try:

            supabase.table(
                "requests"
            ).update({

                "priority":
                    "Moderate",

                "analysis_status":
                    "completed"

            }).eq(
                "id",
                request_id
            ).execute()

        except Exception as db_error:

            print(
                "Fallback database error:",
                db_error
            )


# =========================================================
# HOME
# =========================================================

@app.route("/")
def index():

    if "user_id" in session:

        if is_government_user():

            return redirect(
                url_for(
                    "government_dashboard"
                )
            )

        return redirect(
            url_for("dashboard")
        )


    return render_template(
        "index.html"
    )


# =========================================================
# REGISTER
# =========================================================

@app.route(
    "/register",
    methods=["GET", "POST"]
)
def register():

    if request.method == "POST":

        full_name = request.form.get(
            "full_name",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        face_image = request.form.get(
            "face_image",
            ""
        )


        if not full_name:

            return "Full name is required."


        if not email:

            return "Email is required."


        if not password:

            return "Password is required."


        if not face_image:

            return (
                "Face verification is required."
            )


        profile_filename = None


        try:

            profile_filename = upload_base64_image(
                PROFILE_BUCKET,
                face_image
            )


        except Exception as error:

            print(
                "Profile image error:",
                error
            )

            return (
                "There was a problem "
                "saving the face image."
            )


        try:

            existing = (
                supabase
                .table("users")
                .select("id")
                .eq(
                    "email",
                    email
                )
                .limit(1)
                .execute()
            )


            if existing.data:

                return (
                    "Email already registered."
                )


            supabase.table(
                "users"
            ).insert({

                "full_name":
                    full_name,

                "email":
                    email,

                "password":
                    password,

                "role":
                    "citizen",

                "profile_image":
                    profile_filename

            }).execute()


            return redirect(
                url_for("login")
            )


        except Exception as error:

            print(
                "Registration error:",
                error
            )

            return (
                "Unable to create account."
            )


    return render_template(
        "register.html"
    )


# =========================================================
# LOGIN
# =========================================================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )


        try:

            result = (
                supabase
                .table("users")
                .select("*")
                .eq(
                    "email",
                    email
                )
                .eq(
                    "password",
                    password
                )
                .limit(1)
                .execute()
            )


            user = (
                result.data[0]
                if result.data
                else None
            )


        except Exception as error:

            print(
                "Login error:",
                error
            )

            return (
                "Unable to connect to the database."
            )


        if not user:

            return (
                "Invalid email or password."
            )


        session["user_id"] = user["id"]

        session["full_name"] = (
            user["full_name"]
        )

        session["role"] = (
            user["role"]
        )

        session["profile_image"] = (
            user.get("profile_image")
        )


        if email.endswith(
            "@gabay.gov.ph"
        ):

            if user["role"] in [
                "government",
                "gov_employee",
                "government_employee",
                "admin"
            ]:

                return redirect(
                    url_for(
                        "government_dashboard"
                    )
                )


            return (
                "This GABAY government account "
                "does not have a valid government role."
            ), 403


        return redirect(
            url_for("dashboard")
        )


    return render_template(
        "login.html"
    )


# =========================================================
# GOVERNMENT CRITICAL ALERT
# =========================================================

@app.route(
    "/api/government/critical-alerts"
)
def government_critical_alerts():

    if session.get("role") == "admin":

        return jsonify({

            "success":
                True,

            "alert":
                None,

            "alarm_enabled":
                False

        })


    if not is_government_user():

        return jsonify({
            "error":
                "Unauthorized"
        }), 403


    employee_id = session["user_id"]


    try:

        assigned_result = (
            supabase
            .table("requests")
            .select("id")
            .eq(
                "priority",
                "Critical"
            )
            .eq(
                "assigned_to",
                employee_id
            )
            .or_(
                "forwarded.is.null,forwarded.eq.0"
            )
            .order(
                "id"
            )
            .limit(1)
            .execute()
        )


        assigned = (
            assigned_result.data[0]
            if assigned_result.data
            else None
        )


        if not assigned:

            candidate_result = (
                supabase
                .table("requests")
                .select("id,assigned_to,forwarded")
                .eq(
                    "priority",
                    "Critical"
                )
                .or_(
                    "forwarded.is.null,forwarded.eq.0"
                )
                .is_(
                    "assigned_to",
                    "null"
                )
                .order(
                    "id"
                )
                .limit(1)
                .execute()
            )


            candidate = (
                candidate_result.data[0]
                if candidate_result.data
                else None
            )


                        if candidate:

                try:

                    supabase.table(
                        "requests"
                    ).update({

                        "assigned_to":
                            employee_id

                    }).eq(
                        "id",
                        candidate["id"]
                    ).execute()

                    print(
                        "Critical request automatically "
                        "assigned to government employee:",
                        employee_id
                    )

                    return jsonify({

                        "success":
                            True,

                        "alert":
                            candidate,

                        "alarm_enabled":
                            True

                    })

                except Exception as error:

                    print(
                        "Critical request assignment error:",
                        error
                    )


        return jsonify({

            "success":
                True,

            "alert":
                None,

            "alarm_enabled":
                False

        })


    except Exception as error:

        print(
            "Critical alert error:",
            error
        )

        return jsonify({

            "success":
                False,

            "error":
                "Unable to check critical alerts.",

            "alarm_enabled":
                False

        }), 500


# =========================================================
# DASHBOARD
# =========================================================

@app.route("/dashboard")
def dashboard():

    if "user_id" not in session:

        return redirect(
            url_for("login")
        )

    if is_government_user():

        return redirect(
            url_for("government_dashboard")
        )

    return render_template(
        "dashboard.html"
    )


# =========================================================
# GOVERNMENT DASHBOARD
# =========================================================

@app.route("/government")
@app.route("/government/dashboard")
def government_dashboard():

    if not is_government_user():

        return redirect(
            url_for("login")
        )

    return render_template(
        "government_dashboard.html"
    )


# =========================================================
# ADMIN DASHBOARD
# =========================================================

@app.route("/admin")
@app.route("/admin/dashboard")
def admin_dashboard():

    if not is_admin_user():

        return redirect(
            url_for("login")
        )

    return render_template(
        "admin_dashboard.html"
    )


# =========================================================
# CREATE REQUEST
# =========================================================

@app.route(
    "/api/requests",
    methods=["POST"]
)
def create_request():

    if "user_id" not in session:

        return jsonify({
            "error":
                "Unauthorized"
        }), 401


    if is_government_user():

        return jsonify({
            "error":
                "Government accounts cannot submit citizen requests."
        }), 403


    require_supabase()


    try:

        data = request.get_json(
            silent=True
        ) or {}


        category = str(
            data.get(
                "category",
                "General Assistance"
            )
        ).strip()


        description = str(
            data.get(
                "description",
                ""
            )
        ).strip()


        location = str(
            data.get(
                "location",
                ""
            )
        ).strip()


        if not description:

            return jsonify({

                "success":
                    False,

                "error":
                    "Request description is required."

            }), 400


        insert_data = {

            "user_id":
                session["user_id"],

            "category":
                category,

            "description":
                description,

            "location":
                location,

            "priority":
                "Moderate",

            "analysis_status":
                "pending",

            "status":
                "Pending",

            "assigned_to":
                None,

            "forwarded":
                False

        }


                result = (
            supabase
            .table("requests")
            .insert(insert_data)
            .execute()
        )


        if not result.data:

            return (
                "Unable to create request."
            )


        created_request = result.data[0]

        request_id = created_request["id"]


        # =================================================
        # SAVE OPTIONAL REPORT IMAGE
        # =================================================

        if image_data:

            try:

                report_filename = (
                    upload_base64_image(
                        REPORT_BUCKET,
                        image_data
                    )
                )


                (
                    supabase
                    .table("requests")
                    .update({
                        "image":
                            report_filename
                    })
                    .eq(
                        "id",
                        request_id
                    )
                    .execute()
                )


            except Exception as image_error:

                print(
                    "Report image upload error:",
                    image_error
                )


        # =================================================
        # START GEMINI AI ANALYSIS
        # =================================================

        analysis_thread = threading.Thread(
            target=analyze_request_with_gemini,
            args=(
                request_id,
                category,
                description
            ),
            daemon=True
        )

        analysis_thread.start()


        # =================================================
        # RETURN TO CITIZEN DASHBOARD
        # =================================================

        return redirect(
            url_for("dashboard")
        )


    except Exception as error:

        print(
            "Request creation error:",
            error
        )

        return (
            "Unable to submit your request."
        ), 500


    return render_template(
        "request.html"
    )
