from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    send_from_directory,
    jsonify
)

import sqlite3
import os
import base64
import uuid
import threading

from datetime import datetime
from google import genai


# =========================================================
# GABAY APPLICATION
# =========================================================

app = Flask(__name__)

app.secret_key = "gabay-prototype-secret-key"


# =========================================================
# GEMINI CONFIGURATION
# =========================================================

# The key is loaded from the database when available so an administrator
# can change it from the dashboard without rebuilding the application/APK.
# The environment variable remains the initial fallback for first setup.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
gemini_client = None


GEMINI_MODEL = "gemini-3.6-flash"


def configure_gemini_client(api_key):

    """Apply a Gemini API key to the running application."""

    global GEMINI_API_KEY, gemini_client

    GEMINI_API_KEY = (api_key or "").strip()

    if GEMINI_API_KEY:

        gemini_client = genai.Client(
            api_key=GEMINI_API_KEY
        )

    else:

        gemini_client = None


def get_configured_gemini_key():

    """Return the persisted key, falling back to the environment key."""

    try:

        connection = get_db()

        setting = connection.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            ("gemini_api_key",)
        ).fetchone()

        connection.close()

        if setting and setting["value"]:
            return setting["value"].strip()

    except Exception as error:

        print("Gemini settings load error:", error)

    return (os.environ.get("GEMINI_API_KEY") or "").strip()


# =========================================================
# DATABASE SETTINGS
# =========================================================

DATABASE = "database.db"

PROFILE_FOLDER = os.path.join(
    "uploads",
    "profiles"
)

VERIFICATION_FOLDER = os.path.join(
    "uploads",
    "verification"
)

REPORT_FOLDER = os.path.join(
    "uploads",
    "reports"
)


os.makedirs(
    PROFILE_FOLDER,
    exist_ok=True
)

os.makedirs(
    VERIFICATION_FOLDER,
    exist_ok=True
)

os.makedirs(
    REPORT_FOLDER,
    exist_ok=True
)


# =========================================================
# PROTOTYPE GOVERNMENT DEPARTMENTS
# =========================================================
#
# IMPORTANT:
# These are prototype contacts only.
# No actual SMS or email will be sent.
#
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
# DATABASE CONNECTION
# =========================================================

def get_db():

    connection = sqlite3.connect(
        DATABASE
    )

    connection.row_factory = sqlite3.Row

    return connection


# =========================================================
# CHECK GOVERNMENT ROLE
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


# =========================================================
# INITIALIZE DATABASE
# =========================================================

def initialize_database():

    connection = get_db()


    # =====================================================
    # USERS
    # =====================================================

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS users (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            full_name TEXT NOT NULL,

            email TEXT UNIQUE NOT NULL,

            password TEXT NOT NULL,

            role TEXT NOT NULL DEFAULT 'citizen',

            profile_image TEXT

        )
        """
    )


    # =====================================================
    # APPLICATION SETTINGS
    # =====================================================

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )
        """
    )

    # Preserve the current environment key as the initial value only.
    # An administrator can later replace it from the dashboard.
    existing_gemini_setting = connection.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        ("gemini_api_key",)
    ).fetchone()

    if not existing_gemini_setting and os.environ.get("GEMINI_API_KEY"):
        connection.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            """,
            (
                "gemini_api_key",
                os.environ.get("GEMINI_API_KEY", "").strip(),
                datetime.now().isoformat(timespec="seconds")
            )
        )

    # =====================================================
    # DEFAULT GOVERNMENT ACCOUNT
    # =====================================================

    government_account = connection.execute(
        """
        SELECT id
        FROM users
        WHERE email = ?
        """,
        (
            "government@gabay.gov.ph",
        )
    ).fetchone()


    if not government_account:

        connection.execute(
            """
            INSERT INTO users
            (
                full_name,
                email,
                password,
                role,
                profile_image
            )

            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "GABAY Government Personnel",
                "government@gabay.gov.ph",
                "Gov12345!",
                "government",
                None
            )
        )


    # =====================================================
    # REQUESTS
    # =====================================================

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS requests (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            ticket_number TEXT UNIQUE NOT NULL,

            user_id INTEGER NOT NULL,

            category TEXT NOT NULL,

            description TEXT NOT NULL,

            contact_number TEXT NOT NULL,

            email TEXT,

            address TEXT NOT NULL,

            latitude TEXT,

            longitude TEXT,

            priority TEXT NOT NULL DEFAULT 'Analyzing',

            status TEXT NOT NULL DEFAULT 'Pending',

            assigned_department TEXT,

            verification_image TEXT,

            created_at TEXT NOT NULL,

            analysis_status TEXT DEFAULT 'completed',

            FOREIGN KEY (user_id)
            REFERENCES users(id)

        )
        """
    )


    # =====================================================
    # DATABASE MIGRATION
    # =====================================================

    columns = connection.execute(
        "PRAGMA table_info(requests)"
    ).fetchall()


    column_names = [
        column["name"]
        for column in columns
    ]


    # -----------------------------------------------------
    # verification_image
    # -----------------------------------------------------

    if "verification_image" not in column_names:

        connection.execute(
            """
            ALTER TABLE requests
            ADD COLUMN verification_image TEXT
            """
        )


    # -----------------------------------------------------
    # analysis_status
    # -----------------------------------------------------

    if "analysis_status" not in column_names:

        connection.execute(
            """
            ALTER TABLE requests
            ADD COLUMN analysis_status TEXT
            DEFAULT 'completed'
            """
        )


    # -----------------------------------------------------
    # forwarded
    # -----------------------------------------------------

    if "forwarded" not in column_names:

        connection.execute(
            """
            ALTER TABLE requests
            ADD COLUMN forwarded INTEGER
            DEFAULT 0
            """
        )


    # -----------------------------------------------------
    # forward_method
    # -----------------------------------------------------

    if "forward_method" not in column_names:

        connection.execute(
            """
            ALTER TABLE requests
            ADD COLUMN forward_method TEXT
            """
        )


    # -----------------------------------------------------
    # forward_target
    # -----------------------------------------------------

    if "forward_target" not in column_names:

        connection.execute(
            """
            ALTER TABLE requests
            ADD COLUMN forward_target TEXT
            """
        )


    # -----------------------------------------------------
    # forwarded_by
    # -----------------------------------------------------

    if "forwarded_by" not in column_names:

        connection.execute(
            """
            ALTER TABLE requests
            ADD COLUMN forwarded_by INTEGER
            """
        )


    # -----------------------------------------------------
    # forwarded_at
    # -----------------------------------------------------

    if "forwarded_at" not in column_names:

        connection.execute(
            """
            ALTER TABLE requests
            ADD COLUMN forwarded_at TEXT
            """
        )


    if "assigned_to" not in column_names:

        connection.execute(
            """
            ALTER TABLE requests
            ADD COLUMN assigned_to INTEGER
            """
        )


    if "ai_report" not in column_names:

        connection.execute(
            """
            ALTER TABLE requests
            ADD COLUMN ai_report TEXT
            """
        )


    if "map_screenshot" not in column_names:

        connection.execute(
            """
            ALTER TABLE requests
            ADD COLUMN map_screenshot TEXT
            """
        )


    if "report_attachment" not in column_names:

        connection.execute(
            """
            ALTER TABLE requests
            ADD COLUMN report_attachment TEXT
            """
        )


    if "report_submitted_at" not in column_names:

        connection.execute(
            """
            ALTER TABLE requests
            ADD COLUMN report_submitted_at TEXT
            """
        )


    connection.commit()

    connection.close()

    # Apply the persisted key to this running Flask process.
    configure_gemini_client(get_configured_gemini_key())


# =========================================================
# GEMINI SEVERITY ANALYSIS
# =========================================================

def analyze_request_with_gemini(
    request_id,
    category,
    description
):

    configure_gemini_client(get_configured_gemini_key())

    print()
    print("========================================")
    print("GABAY GEMINI BACKGROUND ANALYSIS")
    print("Request ID:", request_id)
    print("Category:", category)
    print("Description:", description)


    try:

        # =================================================
        # CHECK API KEY
        # =================================================

        if not GEMINI_API_KEY or not gemini_client:

            print(
                "Gemini AI: API key not configured."
            )

            connection = get_db()

            connection.execute(
                """
                UPDATE requests

                SET priority = ?,
                    analysis_status = ?

                WHERE id = ?
                """,
                (
                    "Moderate",
                    "completed",
                    request_id
                )
            )

            connection.commit()

            connection.close()

            print(
                "FINAL PRIORITY: Moderate"
            )

            print(
                "========================================"
            )

            return


        # =================================================
        # GEMINI PROMPT
        # =================================================

        prompt = f"""

You are the emergency severity classification AI
for a Philippine government assistance system called GABAY.

Your job is to analyze the citizen's request and determine
how urgent and severe the situation is.

The citizen may write using:

- English
- Filipino
- Tagalog
- Bisaya/Cebuano
- mixed English and Filipino
- slang
- informal grammar
- uppercase/lowercase text
- short descriptions

You MUST understand the meaning of the situation rather
than relying only on keywords.

REQUEST CATEGORY:

{category}

CITIZEN DESCRIPTION:

{description}


SEVERITY LEVELS:


CRITICAL

Use Critical when the situation involves immediate danger
to life, possible death, serious injury, or an active emergency.

Examples include:

- heart attack
- person not responding
- person unconscious
- person not breathing
- drowning
- severe bleeding
- active house fire
- multiple houses on fire
- trapped people
- building collapse
- major accident
- people trapped in danger
- active flood threatening lives
- immediate threat to multiple people
- emergency requiring immediate response


HIGH

Use High when the situation is serious and requires urgent
government response but there is no clear immediate
life-threatening danger.


MODERATE

Use Moderate for legitimate government assistance requests
that require action but are not immediately dangerous.

Examples:

- normal assistance requests
- non-emergency medical concerns
- water service problems
- minor infrastructure problems
- ordinary complaints
- assistance requests that can wait


LOW

Use Low for non-urgent matters such as:

- information requests
- documents
- routine government services
- simple inquiries
- minor non-emergency concerns


IMPORTANT:

If a person may die or suffer serious harm without immediate
action, classify the request as CRITICAL.

For example:

"Inatake sa puso asawa ko di na siya nag rerepose"

means:

"My spouse had a heart attack and is no longer responding."

That MUST be classified as Critical.


Another example:

"TULONG YUNG ASAWA NALUNOD DI NA SIYA NAGRERESPONSE"

means:

"Help, my spouse drowned and is no longer responding."

That MUST be classified as Critical.


Another example:

"BIG HOUSE FIRE FIVE HOUSES ARE ALREADY ON FIRE"

must be classified as Critical.


RETURN ONLY ONE OF THESE FOUR WORDS:

Critical
High
Moderate
Low

Do not explain your answer.

Do not return punctuation.

Do not return additional text.

"""


        # =================================================
        # SEND TO GEMINI
        # =================================================

        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt
        )


        # =================================================
        # GET RESPONSE
        # =================================================

        severity_response = (
            response.text.strip()
            if response.text
            else ""
        )


        print(
            "Gemini severity response:",
            severity_response
        )


        # =================================================
        # NORMALIZE RESPONSE
        # =================================================

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

            print(
                "Gemini returned an unknown severity."
            )

            priority = "Moderate"


        # =================================================
        # UPDATE DATABASE
        # =================================================

        connection = get_db()

        connection.execute(
            """
            UPDATE requests

            SET priority = ?,
                analysis_status = ?

            WHERE id = ?
            """,
            (
                priority,
                "completed",
                request_id
            )
        )

        connection.commit()

        connection.close()


        print(
            "FINAL PRIORITY:",
            priority
        )

        print(
            "========================================"
        )

        print()


    except Exception as error:

        print(
            "Gemini severity analysis error:",
            error
        )


        # =================================================
        # IF GEMINI FAILS
        # =================================================

        connection = get_db()

        connection.execute(
            """
            UPDATE requests

            SET priority = ?,
                analysis_status = ?

            WHERE id = ?
            """,
            (
                "Moderate",
                "completed",
                request_id
            )
        )

        connection.commit()

        connection.close()


        print(
            "Gemini failed."
        )

        print(
            "FINAL PRIORITY: Moderate"
        )

        print(
            "========================================"
        )

        print()


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


        # =================================================
        # VALIDATION
        # =================================================

        if not full_name:

            return "Full name is required."


        if not email:

            return "Email is required."


        if not password:

            return "Password is required."


        # =================================================
        # SAVE PROFILE FACE IMAGE
        # =================================================

        profile_filename = None


        if face_image:

            try:

                if "," in face_image:

                    image_data = face_image.split(
                        ",",
                        1
                    )[1]

                else:

                    image_data = face_image


                image_bytes = base64.b64decode(
                    image_data
                )


                profile_filename = (
                    f"{uuid.uuid4().hex}.jpg"
                )


                profile_path = os.path.join(
                    PROFILE_FOLDER,
                    profile_filename
                )


                with open(
                    profile_path,
                    "wb"
                ) as image_file:

                    image_file.write(
                        image_bytes
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


        # =================================================
        # INSERT USER
        # =================================================

        connection = get_db()


        try:

            connection.execute(
                """
                INSERT INTO users
                (
                    full_name,
                    email,
                    password,
                    role,
                    profile_image
                )

                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    full_name,
                    email,
                    password,
                    "citizen",
                    profile_filename
                )
            )


            connection.commit()

            connection.close()


            return redirect(
                url_for("login")
            )


        except sqlite3.IntegrityError:

            connection.close()

            return (
                "Email already registered."
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


        connection = get_db()


        user = connection.execute(
            """
            SELECT *
            FROM users
            WHERE email = ?
            AND password = ?
            """,
            (
                email,
                password
            )
        ).fetchone()


        connection.close()


        # =================================================
        # INVALID LOGIN
        # =================================================

        if not user:

            return "Invalid email or password."


        # =================================================
        # SAVE LOGIN SESSION
        # =================================================

        session["user_id"] = user["id"]

        session["full_name"] = (
            user["full_name"]
        )

        session["role"] = (
            user["role"]
        )

        session["profile_image"] = (
            user["profile_image"]
        )


        # =================================================
        # GOVERNMENT LOGIN
        # =================================================

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


        # =================================================
        # CITIZEN LOGIN
        # =================================================

        return redirect(
            url_for("dashboard")
        )


    return render_template(
        "login.html"
    )


# =========================================================
# GOVERNMENT CRITICAL ALERT API
# =========================================================

@app.route(
    "/api/government/critical-alerts"
)
def government_critical_alerts():

    # CRITICAL ALARMS ARE FOR GOVERNMENT EMPLOYEES ONLY.
    # Administrators may access the dashboard/settings, but they must
    # never be assigned an emergency and must never receive the alarm.
    if session.get("role") == "admin":
        return jsonify({
            "success": True,
            "alert": None,
            "alarm_enabled": False
        })

    if not is_government_user():

        if "user_id" not in session:

            return jsonify({
                "error": "Unauthorized"
            }), 401

        return jsonify({
            "error": "Unauthorized"
        }), 403


    employee_id = session["user_id"]
    connection = get_db()

    try:

        # Only one employee can own an emergency.
        assigned = connection.execute(
            """
            SELECT id
            FROM requests
            WHERE priority = 'Critical'
              AND (forwarded IS NULL OR forwarded = 0)
              AND assigned_to = ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (employee_id,)
        ).fetchone()


        if not assigned:

            candidate = connection.execute(
                """
                SELECT id
                FROM requests
                WHERE priority = 'Critical'
                  AND (forwarded IS NULL OR forwarded = 0)
                  AND (assigned_to IS NULL OR assigned_to = 0)
                ORDER BY id ASC
                LIMIT 1
                """
            ).fetchone()


            if candidate:

                connection.execute(
                    """
                    UPDATE requests
                    SET assigned_to = ?
                    WHERE id = ?
                      AND (assigned_to IS NULL OR assigned_to = 0)
                      AND (forwarded IS NULL OR forwarded = 0)
                    """,
                    (
                        employee_id,
                        candidate["id"]
                    )
                )

                connection.commit()

                assigned = connection.execute(
                    """
                    SELECT id
                    FROM requests
                    WHERE id = ?
                      AND assigned_to = ?
                      AND (forwarded IS NULL OR forwarded = 0)
                    """,
                    (
                        candidate["id"],
                        employee_id
                    )
                ).fetchone()


        if not assigned:

            return jsonify({
                "success": True,
                "alert": None
            })


        critical = connection.execute(
            """
            SELECT
                requests.id,
                requests.ticket_number,
                requests.category,
                requests.description,
                requests.priority,
                requests.status,
                requests.created_at,
                requests.forwarded,
                requests.address,
                requests.latitude,
                requests.longitude,
                requests.ai_report,
                requests.map_screenshot,
                users.full_name
            FROM requests
            LEFT JOIN users
                ON requests.user_id = users.id
            WHERE requests.id = ?
              AND requests.assigned_to = ?
              AND (requests.forwarded IS NULL OR requests.forwarded = 0)
            LIMIT 1
            """,
            (
                assigned["id"],
                employee_id
            )
        ).fetchone()


        if not critical:

            return jsonify({
                "success": True,
                "alert": None
            })


        return jsonify({

            "success": True,

            "alert": {

                "id": critical["id"],

                "ticket_number":
                    critical["ticket_number"],

                "category":
                    critical["category"],

                "description":
                    critical["description"],

                "priority":
                    critical["priority"],

                "status":
                    critical["status"],

                "created_at":
                    critical["created_at"],

                "forwarded":
                    bool(critical["forwarded"]),

                "full_name":
                    critical["full_name"],

                "address":
                    critical["address"],

                "latitude":
                    critical["latitude"],

                "longitude":
                    critical["longitude"],

                "ai_report":
                    critical["ai_report"],

                "map_screenshot":
                    critical["map_screenshot"],

                "assigned_to_me":
                    True

            }

        })

    finally:

        connection.close()


# =========================================================
# GOVERNMENT LIVE UPDATE API
# =========================================================
#
# The government dashboard will call this endpoint
# every few seconds.
#
# =========================================================

@app.route(
    "/api/government/dashboard-updates"
)
def government_dashboard_updates():

    if not is_government_user():

        if "user_id" not in session:

            return jsonify({
                "error": "Unauthorized"
            }), 401

        return jsonify({
            "error": "Unauthorized"
        }), 403


    connection = get_db()


    # =====================================================
    # TOTAL REQUESTS
    # =====================================================

    total_requests = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM requests
        """
    ).fetchone()["total"]


    # =====================================================
    # CRITICAL REQUESTS
    # =====================================================

    critical_requests = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM requests
        WHERE priority = 'Critical'
        """
    ).fetchone()["total"]


    # =====================================================
    # HIGH REQUESTS
    # =====================================================

    high_requests = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM requests
        WHERE priority = 'High'
        """
    ).fetchone()["total"]


    # =====================================================
    # MODERATE REQUESTS
    # =====================================================

    moderate_requests = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM requests
        WHERE priority = 'Moderate'
        """
    ).fetchone()["total"]


    # =====================================================
    # LOW REQUESTS
    # =====================================================

    low_requests = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM requests
        WHERE priority = 'Low'
        """
    ).fetchone()["total"]


    # =====================================================
    # PENDING
    # =====================================================

    pending_requests = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM requests
        WHERE status = 'Pending'
        """
    ).fetchone()["total"]


    # =====================================================
    # IN PROGRESS
    # =====================================================

    in_progress_requests = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM requests
        WHERE status = 'In Progress'
        """
    ).fetchone()["total"]


    # =====================================================
    # RESOLVED
    # =====================================================

    resolved_requests = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM requests
        WHERE status = 'Resolved'
        """
    ).fetchone()["total"]


    # =====================================================
    # LATEST REQUEST
    # =====================================================

    latest_request = connection.execute(
        """
        SELECT

            requests.id,

            requests.ticket_number,

            requests.category,

            requests.description,

            requests.priority,

            requests.status,

            requests.created_at,

            requests.forwarded,

            users.full_name

        FROM requests

        LEFT JOIN users
            ON requests.user_id = users.id

        ORDER BY requests.id DESC

        LIMIT 1
        """
    ).fetchone()


    # =====================================================
    # ACTIVE CRITICAL ALARM
    # =====================================================
    #
    # IMPORTANT:
    #
    # The alarm remains active if there is ANY Critical
    # request that has NOT been forwarded.
    #
    # =====================================================

    active_alarm = connection.execute(
        """
        SELECT

            requests.id,

            requests.ticket_number,

            requests.category,

            requests.description,

            requests.priority,

            requests.status,

            requests.created_at,

            requests.forwarded,

            users.full_name

        FROM requests

        LEFT JOIN users
            ON requests.user_id = users.id

        WHERE requests.priority = 'Critical'

        AND (
            requests.forwarded IS NULL
            OR requests.forwarded = 0
        )

        ORDER BY requests.id DESC

        LIMIT 1
        """
    ).fetchone()


    connection.close()


    latest_data = None


    if latest_request:

        latest_data = {

            "id":
                latest_request["id"],

            "ticket_number":
                latest_request["ticket_number"],

            "category":
                latest_request["category"],

            "description":
                latest_request["description"],

            "priority":
                latest_request["priority"],

            "status":
                latest_request["status"],

            "created_at":
                latest_request["created_at"],

            "forwarded":
                bool(latest_request["forwarded"]),

            "full_name":
                latest_request["full_name"]

        }


    alarm_data = None


    if active_alarm:

        alarm_data = {

            "id":
                active_alarm["id"],

            "ticket_number":
                active_alarm["ticket_number"],

            "category":
                active_alarm["category"],

            "description":
                active_alarm["description"],

            "priority":
                active_alarm["priority"],

            "status":
                active_alarm["status"],

            "created_at":
                active_alarm["created_at"],

            "forwarded":
                bool(active_alarm["forwarded"]),

            "full_name":
                active_alarm["full_name"]

        }


    return jsonify({

        "success": True,

        "total_requests":
            total_requests,

        "critical_requests":
            critical_requests,

        "high_requests":
            high_requests,

        "moderate_requests":
            moderate_requests,

        "low_requests":
            low_requests,

        "pending_requests":
            pending_requests,

        "in_progress_requests":
            in_progress_requests,

        "resolved_requests":
            resolved_requests,

        "latest_request":
            latest_data,

        "alarm_active":
            active_alarm is not None,

        "alarm_request":
            alarm_data

    })


# =========================================================
# GOVERNMENT DEPARTMENT API
# =========================================================

@app.route(
    "/api/government/departments"
)
def government_departments():

    if not is_government_user():

        if "user_id" not in session:

            return jsonify({
                "error": "Unauthorized"
            }), 401

        return jsonify({
            "error": "Unauthorized"
        }), 403


    departments = []


    for name, contact in GOVERNMENT_DEPARTMENTS.items():

        departments.append({

            "name":
                name,

            "phone":
                contact["phone"],

            "email":
                contact["email"]

        })


    return jsonify({

        "success": True,

        "prototype_mode": True,

        "departments": departments

    })


# =========================================================
# AI WRITTEN EMERGENCY REPORT
# =========================================================

def generate_ai_emergency_report(report):

    prompt = f"""
You are GABAY AI, an emergency coordination assistant
for a Philippine government assistance system.

Write a concise, professional incident report that a
government department can immediately act on.

Use ONLY the supplied information. Do not invent facts.
If information is unavailable, write "Not provided".

Ticket Number: {report["ticket_number"]}
Citizen: {report["full_name"]}
Category: {report["category"]}
Priority: {report["priority"]}
Status: {report["status"]}
Date Submitted: {report["created_at"]}
Address: {report["address"]}
Latitude: {report["latitude"]}
Longitude: {report["longitude"]}

Citizen Description:
{report["description"]}

Return a professional report with these headings:

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
                contents=prompt
            )

            text = (
                response.text.strip()
                if response.text
                else ""
            )

            if text:
                return text

        except Exception as error:

            print(
                "AI report generation error:",
                error
            )


    return f"""GABAY EMERGENCY INCIDENT REPORT

Incident Summary
Ticket Number: {report["ticket_number"]}
Citizen: {report["full_name"]}
Category: {report["category"]}
Date Submitted: {report["created_at"]}

Location
Address: {report["address"]}
Latitude: {report["latitude"]}
Longitude: {report["longitude"]}

Reported Situation
{report["description"]}

Priority Assessment
{report["priority"]}

Recommended Immediate Action
The assigned government department should verify the
reported situation and dispatch the appropriate response
according to its emergency procedures.

Information for Responding Department
This report was generated from the citizen-submitted
GABAY emergency request. The attached map screenshot
shows the reported GPS location.
"""


@app.route(
    "/api/government/ai-report/<int:request_id>"
)
def government_ai_report(request_id):

    if not is_government_user():

        return jsonify({
            "success": False,
            "error": "Unauthorized"
        }), 403


    connection = get_db()

    try:

        report = connection.execute(
            """
            SELECT requests.*, users.full_name
            FROM requests
            LEFT JOIN users
                ON requests.user_id = users.id
            WHERE requests.id = ?
            LIMIT 1
            """,
            (request_id,)
        ).fetchone()


        if not report:

            return jsonify({
                "success": False,
                "error": "Report not found."
            }), 404


        if report["priority"] != "Critical":

            return jsonify({
                "success": False,
                "error":
                    "Only Critical reports receive an emergency report."
            }), 400


        if report["ai_report"]:

            return jsonify({
                "success": True,
                "report": report["ai_report"]
            })


        ai_report = generate_ai_emergency_report(
            report
        )


        connection.execute(
            """
            UPDATE requests
            SET ai_report = ?
            WHERE id = ?
            """,
            (
                ai_report,
                request_id
            )
        )

        connection.commit()


        return jsonify({
            "success": True,
            "report": ai_report
        })

    finally:

        connection.close()


# =========================================================
# FORWARD REQUEST
# =========================================================
#
# IMPORTANT:
#
# This DOES NOT send an actual SMS or email.
#
# It only records the forwarding action in the database.
#
# =========================================================

@app.route(
    "/api/government/forward-request/<int:request_id>",
    methods=["POST"]
)
def forward_request(request_id):

    # =====================================================
    # GOVERNMENT ACCESS
    # =====================================================

    if not is_government_user():

        if "user_id" not in session:

            return jsonify({
                "success": False,
                "error": "Unauthorized"
            }), 401

        return jsonify({
            "success": False,
            "error": "Unauthorized"
        }), 403


    # =====================================================
    # GET REQUEST DATA
    # =====================================================

    connection = get_db()


    report = connection.execute(
        """
        SELECT *

        FROM requests

        WHERE id = ?
        """,
        (
            request_id,
        )
    ).fetchone()


    if not report:

        connection.close()

        return jsonify({

            "success": False,

            "error":
                "Report not found."

        }), 404


    # =====================================================
    # ONLY CRITICAL REQUESTS
    # =====================================================

    if report["priority"] != "Critical":

        connection.close()

        return jsonify({

            "success": False,

            "error":
                "Only Critical reports can be "
                "forwarded through the emergency alarm."

        }), 400


    # =====================================================
    # CHECK IF ALREADY FORWARDED
    # =====================================================

    if report["forwarded"]:

        connection.close()

        return jsonify({

            "success": False,

            "error":
                "This report has already been forwarded."

        }), 400


    # =====================================================
    # GET JSON DATA
    # =====================================================

    data = request.get_json(
        silent=True
    )


    if not data:

        data = {}


    method = str(
        data.get(
            "method",
            ""
        )
    ).strip().lower()


    department = str(
        data.get(
            "department",
            ""
        )
    ).strip()


    # =====================================================
    # VALIDATE METHOD
    # =====================================================

    if method not in [
        "phone",
        "email"
    ]:

        connection.close()

        return jsonify({

            "success": False,

            "error":
                "Please select a valid forwarding method."

        }), 400


    # =====================================================
    # VALIDATE DEPARTMENT
    # =====================================================

    if department not in GOVERNMENT_DEPARTMENTS:

        connection.close()

        return jsonify({

            "success": False,

            "error":
                "Please select a valid government department."

        }), 400


    # =====================================================
    # GET DEPARTMENT CONTACT
    # =====================================================

    department_data = (
        GOVERNMENT_DEPARTMENTS[
            department
        ]
    )


    if method == "phone":

        target = department_data["phone"]

        method_name = "Phone Number"


    else:

        target = department_data["email"]

        method_name = "Email Address"


    # =====================================================
    # PREPARE AI WRITTEN REPORT
    # =====================================================

    ai_report = (
        report["ai_report"]
        if report["ai_report"]
        else generate_ai_emergency_report(report)
    )


    # =====================================================
    # SAVE MAP SCREENSHOT ATTACHMENT
    # =====================================================

    map_screenshot = None

    map_data = data.get(
        "map_screenshot",
        ""
    )


    if (
        isinstance(map_data, str)
        and map_data.startswith("data:image/")
    ):

        try:

            _, encoded = map_data.split(
                ",",
                1
            )

            image_bytes = base64.b64decode(
                encoded,
                validate=True
            )


            if len(image_bytes) <= 8 * 1024 * 1024:

                map_filename = (
                    report["ticket_number"]
                    + "_map.png"
                )

                map_path = os.path.join(
                    REPORT_FOLDER,
                    map_filename
                )


                with open(
                    map_path,
                    "wb"
                ) as image_file:

                    image_file.write(
                        image_bytes
                    )


                map_screenshot = map_path


        except Exception as error:

            print(
                "Map screenshot save error:",
                error
            )


    # =====================================================
    # SAVE WRITTEN REPORT
    # =====================================================

    report_filename = (
        report["ticket_number"]
        + "_emergency_report.txt"
    )

    report_path = os.path.join(
        REPORT_FOLDER,
        report_filename
    )


    with open(
        report_path,
        "w",
        encoding="utf-8"
    ) as report_file:

        report_file.write(
            ai_report
        )


    # =====================================================
    # RECORD FORWARDING
    # =====================================================

    forwarded_at = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


    cursor = connection.execute(
        """
        UPDATE requests

        SET

            forwarded = 1,

            forward_method = ?,

            forward_target = ?,

            assigned_department = ?,

            forwarded_by = ?,

            forwarded_at = ?,

            status = 'In Progress',

            ai_report = ?,

            map_screenshot = ?,

            report_attachment = ?,

            report_submitted_at = ?

        WHERE id = ?

          AND (
                forwarded IS NULL
                OR forwarded = 0
              )

          AND (
                assigned_to = ?
                OR assigned_to IS NULL
                OR assigned_to = 0
              )
        """,
        (
            method_name,
            target,
            department,
            session["user_id"],
            forwarded_at,
            ai_report,
            map_screenshot,
            report_path,
            forwarded_at,
            request_id,
            session["user_id"]
        )
    )


    if cursor.rowcount != 1:

        connection.close()

        return jsonify({

            "success": False,

            "error":
                "This emergency was already handled by another employee."

        }), 409


    connection.commit()

    connection.close()

    # =====================================================
    # SUCCESS MESSAGE
    # =====================================================

    confirmation_message = (
        "This report has been forwarded to "
        + department
        + "."
    )


    print()
    print("========================================")
    print("GABAY PROTOTYPE FORWARD")
    print("========================================")
    print("Ticket:", report["ticket_number"])
    print("Department:", department)
    print("Method:", method_name)
    print("Target:", target)
    print("Forwarded by:", session.get("full_name"))
    print("Time:", forwarded_at)
    print()
    print("SIMULATION MODE")
    print("NO ACTUAL SMS OR EMAIL WAS SENT.")
    print("========================================")
    print()


    return jsonify({

        "success": True,

        "prototype_mode": True,

        "message":
            confirmation_message,

        "ticket_number":
            report["ticket_number"],

        "department":
            department,

        "method":
            method_name,

        "target":
            target,

        "forwarded_at":
            forwarded_at,

        "alarm_should_stop":
            True,

        "ai_report":
            ai_report,

        "report_attachment":
            report_path,

        "map_screenshot":
            map_screenshot,

        "report_submitted":
            True

    })


# =========================================================
# DASHBOARD
# =========================================================

@app.route("/dashboard")
def dashboard():

    if "user_id" not in session:

        return redirect(
            url_for("login")
        )


    return render_template(
        "dashboard.html",

        full_name=session.get(
            "full_name",
            "Citizen"
        ),

        role=session.get(
            "role",
            "citizen"
        ),

        profile_image=session.get(
            "profile_image"
        )
    )


# =========================================================
# ADMIN AI SETTINGS
# =========================================================

def is_admin_user():

    return (
        "user_id" in session
        and session.get("role") == "admin"
    )


@app.route("/api/admin/gemini-settings", methods=["GET"])
def admin_gemini_settings():

    if not is_admin_user():
        return jsonify({
            "success": False,
            "error": "Administrator access required."
        }), 403

    key = get_configured_gemini_key()

    return jsonify({
        "success": True,
        "configured": bool(key),
        "masked_key": (
            "••••••••" + key[-4:]
            if len(key) >= 4
            else ("••••••••" if key else "")
        )
    })


@app.route("/api/admin/gemini-settings", methods=["POST"])
def update_admin_gemini_settings():

    if not is_admin_user():
        return jsonify({
            "success": False,
            "error": "Administrator access required."
        }), 403

    payload = request.get_json(silent=True) or {}
    api_key = str(payload.get("api_key", "")).strip()

    if not api_key:
        return jsonify({
            "success": False,
            "error": "Enter a Gemini API key."
        }), 400

    # Never echo the full key back to the browser.
    connection = get_db()
    connection.execute(
        """
        INSERT INTO app_settings (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (
            "gemini_api_key",
            api_key,
            datetime.now().isoformat(timespec="seconds")
        )
    )
    connection.commit()
    connection.close()

    configure_gemini_client(api_key)

    return jsonify({
        "success": True,
        "message": "Gemini API key updated successfully.",
        "configured": True,
        "masked_key": "••••••••" + api_key[-4:]
    })


@app.route("/api/admin/gemini-settings/clear", methods=["POST"])
def clear_admin_gemini_settings():

    if not is_admin_user():
        return jsonify({
            "success": False,
            "error": "Administrator access required."
        }), 403

    connection = get_db()
    connection.execute(
        "DELETE FROM app_settings WHERE key = ?",
        ("gemini_api_key",)
    )
    connection.commit()
    connection.close()

    configure_gemini_client("")

    return jsonify({
        "success": True,
        "message": "Gemini API key removed from GABAY."
    })


# =========================================================
# GOVERNMENT DASHBOARD
# =========================================================

@app.route("/government/dashboard")
def government_dashboard():

    # =====================================================
    # GOVERNMENT ACCESS ONLY
    # =====================================================

    if "user_id" not in session:

        return redirect(
            url_for("login")
        )


    if not is_government_user():

        return (
            "Unauthorized access.",
            403
        )


    connection = get_db()


    # =====================================================
    # TOTAL REQUESTS
    # =====================================================

    total_requests = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM requests
        """
    ).fetchone()["total"]


    # =====================================================
    # CRITICAL REQUESTS
    # =====================================================

    critical_requests = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM requests
        WHERE priority = 'Critical'
        """
    ).fetchone()["total"]


    # =====================================================
    # HIGH REQUESTS
    # =====================================================

    high_requests = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM requests
        WHERE priority = 'High'
        """
    ).fetchone()["total"]


    # =====================================================
    # MODERATE REQUESTS
    # =====================================================

    moderate_requests = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM requests
        WHERE priority = 'Moderate'
        """
    ).fetchone()["total"]


    # =====================================================
    # LOW REQUESTS
    # =====================================================

    low_requests = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM requests
        WHERE priority = 'Low'
        """
    ).fetchone()["total"]


    # =====================================================
    # PENDING REQUESTS
    # =====================================================

    pending_requests = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM requests
        WHERE status = 'Pending'
        """
    ).fetchone()["total"]


    # =====================================================
    # IN PROGRESS REQUESTS
    # =====================================================

    in_progress_requests = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM requests
        WHERE status = 'In Progress'
        """
    ).fetchone()["total"]


    # =====================================================
    # RESOLVED REQUESTS
    # =====================================================

    resolved_requests = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM requests
        WHERE status = 'Resolved'
        """
    ).fetchone()["total"]


    # =====================================================
    # RECENT REQUESTS
    # =====================================================

    recent_requests = connection.execute(
        """
        SELECT

            requests.id,

            requests.ticket_number,

            requests.category,

            requests.description,

            requests.priority,

            requests.status,

            requests.created_at,

            users.full_name

        FROM requests

        LEFT JOIN users
            ON requests.user_id = users.id

        ORDER BY requests.id DESC

        LIMIT 10
        """
    ).fetchall()


    # =====================================================
    # CRITICAL REQUEST LIST
    # =====================================================

    critical_request_list = connection.execute(
        """
        SELECT

            requests.id,

            requests.ticket_number,

            requests.category,

            requests.description,

            requests.priority,

            requests.status,

            requests.created_at,

            requests.forwarded,

            users.full_name

        FROM requests

        LEFT JOIN users
            ON requests.user_id = users.id

        WHERE requests.priority = 'Critical'

        ORDER BY requests.id DESC

        LIMIT 20
        """
    ).fetchall()


    connection.close()


    # =====================================================
    # RENDER
    # =====================================================

    return render_template(
        "government_dashboard.html",

        total_requests=total_requests,

        critical_requests=critical_requests,

        high_requests=high_requests,

        moderate_requests=moderate_requests,

        low_requests=low_requests,

        pending_requests=pending_requests,

        in_progress_requests=in_progress_requests,

        resolved_requests=resolved_requests,

        recent_requests=recent_requests,

        critical_request_list=
            critical_request_list,

        full_name=session.get(
            "full_name",
            "Government Personnel"
        ),

        profile_image=session.get(
            "profile_image"
        ),

        role=session.get(
            "role",
            "government"
        )
    )


# =========================================================
# REQUEST PAGE
# =========================================================

@app.route(
    "/request/<category>"
)
def request_assistance(category):

    if "user_id" not in session:

        return redirect(
            url_for("login")
        )


    return render_template(
        "request.html",
        category=category
    )


# =========================================================
# SUBMIT REQUEST
# =========================================================

@app.route(
    "/submit-request",
    methods=["POST"]
)
def submit_request():

    if "user_id" not in session:

        return redirect(
            url_for("login")
        )


    # =====================================================
    # GET FORM DATA
    # =====================================================

    category = request.form.get(
        "category",
        "Other"
    ).strip()


    description = request.form.get(
        "description",
        ""
    ).strip()


    contact_number = request.form.get(
        "contact_number",
        ""
    ).strip()


    email = request.form.get(
        "email",
        ""
    ).strip()


    address = request.form.get(
        "address",
        ""
    ).strip()


    latitude = request.form.get(
        "latitude",
        ""
    ).strip()


    longitude = request.form.get(
        "longitude",
        ""
    ).strip()


    verification_image = request.form.get(
        "verification_image",
        ""
    )


    # =====================================================
    # VALIDATION
    # =====================================================

    if not description:

        return (
            "Please describe your problem."
        )


    if not contact_number:

        return (
            "Contact number is required."
        )


    if not address:

        return (
            "Please provide your location."
        )


    if not latitude or not longitude:

        return (
            "GPS location is required."
        )


    if not verification_image:

        return (
            "Face verification is required."
        )


    # =====================================================
    # TICKET NUMBER
    # =====================================================

    ticket_number = (

        "GABAY-"

        + datetime.now().strftime(
            "%Y%m%d"
        )

        + "-"

        + uuid.uuid4()
        .hex[:6]
        .upper()

    )


    # =====================================================
    # DATE/TIME
    # =====================================================

    created_at = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


    # =====================================================
    # SAVE VERIFICATION IMAGE
    # =====================================================

    verification_filename = None


    try:

        if "," in verification_image:

            image_data = (
                verification_image
                .split(",", 1)[1]
            )

        else:

            image_data = verification_image


        image_bytes = base64.b64decode(
            image_data
        )


        verification_filename = (
            f"{uuid.uuid4().hex}.jpg"
        )


        verification_path = os.path.join(
            VERIFICATION_FOLDER,
            verification_filename
        )


        with open(
            verification_path,
            "wb"
        ) as image_file:

            image_file.write(
                image_bytes
            )


    except Exception as error:

        print(
            "Verification image error:",
            error
        )

        return (
            "There was a problem saving "
            "the verification image."
        )


    # =====================================================
    # INSERT REQUEST
    # =====================================================

    connection = get_db()


    cursor = connection.execute(
        """
        INSERT INTO requests
        (
            ticket_number,

            user_id,

            category,

            description,

            contact_number,

            email,

            address,

            latitude,

            longitude,

            priority,

            status,

            verification_image,

            created_at,

            analysis_status,

            forwarded,

            forward_method,

            forward_target,

            forwarded_by,

            forwarded_at

        )

        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            ticket_number,

            session["user_id"],

            category,

            description,

            contact_number,

            email,

            address,

            latitude,

            longitude,

            "Analyzing",

            "Pending",

            verification_filename,

            created_at,

            "processing",

            0,

            None,

            None,

            None,

            None
        )
    )


    request_id = cursor.lastrowid


    connection.commit()

    connection.close()


    # =====================================================
    # START GEMINI IN BACKGROUND
    # =====================================================

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


    # =====================================================
    # SEND USER TO PROCESSING PAGE
    # =====================================================

    return redirect(
        url_for(
            "request_processing",
            request_id=request_id
        )
    )


# =========================================================
# REQUEST PROCESSING PAGE
# =========================================================

@app.route(
    "/request-processing/<int:request_id>"
)
def request_processing(request_id):

    if "user_id" not in session:

        return redirect(
            url_for("login")
        )


    connection = get_db()


    request_data = connection.execute(
        """
        SELECT *

        FROM requests

        WHERE id = ?

        AND user_id = ?
        """,
        (
            request_id,
            session["user_id"]
        )
    ).fetchone()


    connection.close()


    if not request_data:

        return (
            "Request not found.",
            404
        )


    return render_template(
        "request_processing.html",
        request_data=request_data
    )


# =========================================================
# REQUEST STATUS API
# =========================================================

@app.route(
    "/api/request-status/<int:request_id>"
)
def request_status(request_id):

    if "user_id" not in session:

        return jsonify({
            "error": "Unauthorized"
        }), 401


    connection = get_db()


    request_data = connection.execute(
        """
        SELECT

            id,

            ticket_number,

            category,

            description,

            priority,

            status,

            analysis_status,

            created_at

        FROM requests

        WHERE id = ?

        AND user_id = ?
        """,
        (
            request_id,
            session["user_id"]
        )
    ).fetchone()


    connection.close()


    if not request_data:

        return jsonify({
            "error": "Request not found"
        }), 404


    is_ready = (

        request_data["analysis_status"]
        == "completed"

        and request_data["priority"]
        not in (
            None,
            "",
            "Analyzing"
        )

    )


    return jsonify({

        "ready":
            is_ready,

        "id":
            request_data["id"],

        "ticket_number":
            request_data["ticket_number"],

        "category":
            request_data["category"],

        "description":
            request_data["description"],

        "priority":
            request_data["priority"],

        "status":
            request_data["status"],

        "analysis_status":
            request_data["analysis_status"],

        "created_at":
            request_data["created_at"]

    })


# =========================================================
# REQUEST INFORMATION
# =========================================================

@app.route(
    "/request-info/<int:request_id>"
)
def request_info(request_id):

    if "user_id" not in session:

        return redirect(
            url_for("login")
        )


    connection = get_db()


    request_data = connection.execute(
        """
        SELECT *

        FROM requests

        WHERE id = ?

        AND user_id = ?
        """,
        (
            request_id,
            session["user_id"]
        )
    ).fetchone()


    connection.close()


    if not request_data:

        return (
            "Request not found.",
            404
        )


    ticket_number = (
        request_data["ticket_number"]
    )

    category = (
        request_data["category"]
    )

    description = (
        request_data["description"]
    )

    priority = (
        request_data["priority"]
    )

    status = (
        request_data["status"]
    )

    analysis_status = (
        request_data["analysis_status"]
    )

    created_at = (
        request_data["created_at"]
    )


    return render_template(
        "request_success.html",

        request_data=request_data,

        ticket_number=ticket_number,

        category=category,

        description=description,

        priority=priority,

        status=status,

        analysis_status=analysis_status,

        created_at=created_at
    )


# =========================================================
# PROFILE IMAGE
# =========================================================

@app.route(
    "/uploads/profiles/<filename>"
)
def profile_image(filename):

    return send_from_directory(
        PROFILE_FOLDER,
        filename
    )


# =========================================================
# VERIFICATION IMAGE
# =========================================================

@app.route(
    "/uploads/verification/<filename>"
)
def verification_image(filename):

    return send_from_directory(
        VERIFICATION_FOLDER,
        filename
    )


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("index")
    )


# =========================================================
# START APPLICATION
# =========================================================

if __name__ == "__main__":

    initialize_database()

    app.run(
        debug=True
    )