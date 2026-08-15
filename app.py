from dotenv import load_dotenv
load_dotenv()  # loads SMTP_HOST, SMTP_USER, SMTP_PASS, DATABASE_URL, etc. from a .env file automatically

from flask import Flask, jsonify, render_template, request, redirect, session
import psycopg2
import psycopg2.extras
import os
import secrets
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone, date, timedelta
from werkzeug.utils import secure_filename
# =========================
# ADDED FOR CYBERBULLYING DETECTION
# =========================
import joblib
from scipy.sparse import hstack
# =========================
# ADDED FOR NOTIFICATIONS (real-time)
# =========================
from flask_socketio import SocketIO, join_room

app = Flask(__name__)
app.secret_key = "cybersocial_secret_key"
# Upload folder
UPLOAD_FOLDER = "static/uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

MINOR_AGE_CUTOFF = 18

# =========================
# DATABASE (PostgreSQL)
# =========================
# DATABASE_URL comes from your .env locally, and from Render's Postgres
# "Internal Database URL" once deployed (Render injects it automatically
# if you link the database to the web service in the dashboard, or set
# it as an env var yourself).
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Add it to your .env file locally, "
        "or as an environment variable on Render."
    )


def get_conn():
    """Every route opens its own short-lived connection, same pattern as
    the original sqlite3 code — just pointed at Postgres now."""
    return psycopg2.connect(DATABASE_URL)


# =========================
# ADDED FOR PARENT NOTIFICATIONS (email)
# Set these as real environment variables in production (Render dashboard
# -> your service -> Environment). If SMTP isn't configured, emails are
# printed to the console instead of failing, so local dev without a mail
# server still works.
# =========================
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "no-reply@novalink.local")
# IMPORTANT: once deployed, set this to your real Render URL, e.g.
# https://novalink.onrender.com — this is what fixes the parent
# verification links not opening off your home wifi.
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:5000")


def send_email(to_email, subject, body_html):
    """Sends a real email if SMTP env vars are set, otherwise just logs
    it to the console so local dev doesn't need a mail server."""

    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        print("=" * 60)
        print(f"[DEV MODE] Would send email to: {to_email}")
        print(f"Subject: {subject}")
        print(body_html)
        print("=" * 60)
        return

    msg = MIMEText(body_html, "html")
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = to_email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(FROM_EMAIL, [to_email], msg.as_string())


# =========================
# TIME FORMATTING
# =========================
# Postgres TIMESTAMP columns come back from psycopg2 as real Python
# datetime objects (not strings like sqlite3 gave us), so this accepts
# either a datetime or a string, for safety.
def time_ago(timestamp):
    if not timestamp:
        return "Just now"

    if isinstance(timestamp, str):
        try:
            ts = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return timestamp
    else:
        ts = timestamp

    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    seconds = (now - ts).total_seconds()

    if seconds < 0:
        seconds = 0

    if seconds < 60:
        return "Just now"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    elif seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    elif seconds < 604800:
        return f"{int(seconds // 86400)}d ago"
    else:
        return ts.strftime("%b %d, %Y")


def calculate_age(dob_str):
    """dob_str is 'YYYY-MM-DD' from an HTML date input."""
    dob = datetime.strptime(dob_str, "%Y-%m-%d").date()
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


# SocketIO instance used to push real-time notifications to browsers.
# async_mode="threading" avoids needing eventlet/gevent as an extra
# dependency — fine for a project of this size and simplest to deploy.
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# =========================
# CYBERBULLYING MODEL (word + char vectorizers)
# =========================
try:
    model = joblib.load("cyberbullying_model.pkl")

    vectorizers = joblib.load("vectorizer.pkl")
    word_vectorizer = vectorizers["word"]
    char_vectorizer = vectorizers["char"]

    print("✅ Cyberbullying model + word/char vectorizers loaded")

except Exception as e:
    print("❌ Model loading failed:", e)
    model = None
    word_vectorizer = None
    char_vectorizer = None


def classify_text(text):
    """Runs the word+char vectorizer pipeline and returns the model's
    prediction, or None if the model isn't loaded."""
    if model is None or word_vectorizer is None or char_vectorizer is None:
        return None

    word_vec = word_vectorizer.transform([text])
    char_vec = char_vectorizer.transform([text])
    text_vec = hstack([word_vec, char_vec])

    return model.predict(text_vec)[0]


# =========================
# DATABASE SETUP (runs automatically on every startup — this is what
# makes a fresh Render deployment self-provision with zero manual
# scripts. Every statement is safe to re-run.)
# =========================
def init_base_tables():
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            bio TEXT,
            profile_image TEXT,
            cover_image TEXT,
            location TEXT,
            website TEXT
        )
    """)

    # If `users` already existed from an earlier setup (e.g. an older
    # init_db.py run that only created id/username/email/password), the
    # CREATE TABLE above is a no-op and these columns would silently be
    # missing. These ALTER statements are safe to re-run every startup
    # and guarantee the columns exist either way.
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS bio TEXT")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_image TEXT")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS cover_image TEXT")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS location TEXT")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS website TEXT")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            content TEXT,
            image TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id SERIAL PRIMARY KEY,
            post_id INTEGER NOT NULL REFERENCES posts(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            comment TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS likes (
            id SERIAL PRIMARY KEY,
            post_id INTEGER NOT NULL REFERENCES posts(id),
            user_id INTEGER NOT NULL REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS followers (
            id SERIAL PRIMARY KEY,
            follower_id INTEGER NOT NULL REFERENCES users(id),
            following_id INTEGER NOT NULL REFERENCES users(id)
        )
    """)

    conn.commit()
    conn.close()


init_base_tables()


def init_notifications_table():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id SERIAL PRIMARY KEY,
            recipient_id INTEGER NOT NULL,
            actor_id INTEGER NOT NULL,
            type TEXT NOT NULL,          -- 'follow', 'like', 'comment'
            target_type TEXT,            -- 'post', 'profile'
            target_id INTEGER,
            is_read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


init_notifications_table()


# =========================
# ADDED: MINOR SAFETY TABLES
# Postgres supports "IF NOT EXISTS" on ADD COLUMN directly, so this is
# safe to run every time the app starts without checking existing
# columns first, plus two new tables: one for parent email verification
# tokens, one to log every flagged post/comment from a minor so parents
# can be notified and a history can be shown later.
# =========================
def init_minor_safety_tables():
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS date_of_birth TEXT")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS parent_email TEXT")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_minor INTEGER DEFAULT 0")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS account_status TEXT DEFAULT 'active'")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS parent_consent_status TEXT DEFAULT 'not_required'")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS parent_verification_tokens (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            parent_email TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS moderation_flags (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            content_type TEXT NOT NULL,   -- 'post' | 'comment'
            category TEXT NOT NULL,       -- classifier's prediction label
            flagged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            parent_notified_at TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    conn.commit()
    conn.close()


init_minor_safety_tables()


def send_parent_verification_email(parent_email, token, child_username):
    verify_url = f"{BASE_URL}/verify-parent/{token}"
    body = f"""
        <p>Hello,</p>
        <p>The account <strong>{child_username}</strong> on NovaLink has listed you
        as their parent/guardian.</p>
        <p>To activate the account and enable safety notifications for this child's
        activity, please confirm by clicking below:</p>
        <p><a href="{verify_url}">Confirm and activate account</a></p>
        <p>This link expires in 48 hours. If you did not expect this email, you can
        ignore it and the account will remain inactive.</p>
    """
    send_email(parent_email, "Please confirm your child's NovaLink account", body)


def notify_parent_of_flagged_content(user_id, content_type, category):
    """Called whenever the cyberbullying model blocks a post/comment from a
    user who is a registered minor. Logs the flag and emails the parent
    immediately every time (no suppression window — every flagged attempt
    sends an email, useful for demo/testing so every blocked action is
    visibly confirmed)."""

    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT username, parent_email, is_minor
        FROM users WHERE id=%s
    """, (user_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        return

    username, parent_email, is_minor = row

    if not is_minor or not parent_email:
        conn.close()
        return

    cursor.execute("""
        INSERT INTO moderation_flags(user_id, content_type, category)
        VALUES (%s, %s, %s)
        RETURNING id
    """, (user_id, content_type, category))
    flag_id = cursor.fetchone()[0]
    conn.commit()

    body = f"""
        <p>Hello,</p>
        <p>We wanted to let you know that a {content_type} your child
        (<strong>{username}</strong>) attempted to post on NovaLink was
        automatically blocked by our content moderation system.</p>
        <p><strong>Reason:</strong> flagged as {category}</p>
        <p>The content was not published. This is an automated notification —
        no action is required, but you may want to check in with your child.</p>
    """
    send_email(parent_email, "Safety alert: flagged activity on your child's NovaLink account", body)

    cursor.execute("""
        UPDATE moderation_flags SET parent_notified_at = CURRENT_TIMESTAMP
        WHERE id=%s
    """, (flag_id,))
    conn.commit()
    conn.close()


def create_notification(recipient_id, actor_id, type, target_type=None, target_id=None):
    """Insert a notification row and push it live via Socket.IO if the
    recipient is connected. Never notifies a user about their own action."""

    if recipient_id == actor_id:
        return

    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO notifications(recipient_id, actor_id, type, target_type, target_id)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
    """, (recipient_id, actor_id, type, target_type, target_id))
    notif_id = cursor.fetchone()[0]
    conn.commit()

    # get actor username + this notification's timestamp for the payload
    cursor.execute("SELECT username FROM users WHERE id=%s", (actor_id,))
    actor_row = cursor.fetchone()
    actor_username = actor_row[0] if actor_row else "Someone"

    cursor.execute("SELECT created_at FROM notifications WHERE id=%s", (notif_id,))
    created_at = cursor.fetchone()[0]

    conn.close()

    payload = {
        "id": notif_id,
        "type": type,
        "actor_username": actor_username,
        "target_type": target_type,
        "target_id": target_id,
        "created_at": time_ago(created_at),
    }

    # push instantly to the recipient if they're online (room = user id)
    socketio.emit("new_notification", payload, room=str(recipient_id))


# =========================
# HOME
# =========================
@app.route("/")
def home():
    return render_template("index.html")


# =========================
# SIGNUP
# =========================
@app.route("/signup", methods=["GET", "POST"])
def signup():

    if request.method == "POST":

        username = request.form["username"].strip()
        email = request.form["email"].strip()
        password = request.form["password"]
        date_of_birth = request.form.get("date_of_birth", "").strip()
        parent_email = request.form.get("parent_email", "").strip()

        if not date_of_birth:
            return "Date of birth is required"

        try:
            age = calculate_age(date_of_birth)
        except ValueError:
            return "Invalid date of birth"

        is_minor = age < MINOR_AGE_CUTOFF

        if is_minor and not parent_email:
            return "A parent/guardian email is required for users under 18"

        if is_minor and parent_email.lower() == email.lower():
            return "Parent/guardian email must be different from your account email"

        conn = get_conn()
        cursor = conn.cursor()

        # check username
        cursor.execute("SELECT * FROM users WHERE username=%s", (username,))
        if cursor.fetchone():
            conn.close()
            return "Username already exists"

        # check email
        cursor.execute("SELECT * FROM users WHERE email=%s", (email,))
        if cursor.fetchone():
            conn.close()
            return "Email already exists"

        account_status = "pending_consent" if is_minor else "active"
        parent_consent_status = "pending" if is_minor else "not_required"

        # insert user
        cursor.execute("""
            INSERT INTO users(
                username, email, password,
                date_of_birth, parent_email, is_minor,
                account_status, parent_consent_status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            username, email, password,
            date_of_birth, parent_email if is_minor else None, int(is_minor),
            account_status, parent_consent_status
        ))

        user_id = cursor.fetchone()[0]
        conn.commit()

        if is_minor:
            token = secrets.token_hex(32)
            # token is valid for 48 hours
            expires_at = datetime.now(timezone.utc) + timedelta(hours=48)

            cursor.execute("""
                INSERT INTO parent_verification_tokens(user_id, token, parent_email, expires_at)
                VALUES (%s, %s, %s, %s)
            """, (user_id, token, parent_email, expires_at))
            conn.commit()
            conn.close()

            send_parent_verification_email(parent_email, token, username)

            return """
                <h2>Account created!</h2>
                <p>Since you're under 18, we sent an email to your parent/guardian.
                Your account will be activated once they confirm.</p>
                <a href="/login">Go to login</a>
            """

        conn.close()
        return redirect("/login")

    return render_template("signup.html")


# =========================
# VERIFY PARENT (ADDED)
# =========================
@app.route("/verify-parent/<token>")
def verify_parent(token):

    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM parent_verification_tokens
        WHERE token=%s AND used_at IS NULL
    """, (token,))
    record = cursor.fetchone()

    if not record:
        conn.close()
        return "This verification link is invalid or has already been used."

    # columns: id, user_id, token, parent_email, expires_at, used_at, created_at
    record_id, user_id, _, _, expires_at, _, _ = record

    now = datetime.now(timezone.utc)
    expires_at_cmp = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)

    if expires_at_cmp < now:
        conn.close()
        return "This verification link has expired. Please ask your child to sign up again."

    cursor.execute("""
        UPDATE users SET account_status='active', parent_consent_status='verified'
        WHERE id=%s
    """, (user_id,))

    cursor.execute("""
        UPDATE parent_verification_tokens SET used_at=CURRENT_TIMESTAMP
        WHERE id=%s
    """, (record_id,))

    conn.commit()
    conn.close()

    return "Thank you! The account has been verified and activated. <a href='/login'>Go to login</a>"


# =========================
# LOGIN
# =========================
@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form["email"]
        password = request.form["password"]

        conn = get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM users
            WHERE email=%s AND password=%s
        """, (email, password))

        user = cursor.fetchone()

        if user:
            # ADDED: block login while a minor's account is awaiting
            # parent verification. Looked up by name (not index position)
            # to stay safe regardless of column order.
            cursor.execute("SELECT account_status FROM users WHERE id=%s", (user[0],))
            status_row = cursor.fetchone()
            conn.close()

            if status_row and status_row[0] == "pending_consent":
                return "This account is awaiting parent/guardian verification. Ask them to check their email."

            session["user_id"] = user[0]
            session["username"] = user[1]
            return redirect("/feed")

        conn.close()
        return "Invalid credentials"

    return render_template("login.html")


# =========================
# FEED
# =========================
@app.route("/feed")
def feed():

    if "user_id" not in session:
        return redirect("/login")

    conn = get_conn()
    cursor = conn.cursor()

    # posts (feed)
    cursor.execute("""
SELECT
    users.username,
    posts.content,
    posts.id,

    -- Like count
    (
        SELECT COUNT(*)
        FROM likes
        WHERE likes.post_id = posts.id
    ) AS likes,

    posts.created_at,
    posts.image,

    -- Comment count
    (
        SELECT COUNT(*)
        FROM comments
        WHERE comments.post_id = posts.id
    ) AS comment_count

FROM posts
JOIN users
ON posts.user_id = users.id

WHERE posts.user_id = %s
OR posts.user_id IN (
    SELECT following_id
    FROM followers
    WHERE follower_id = %s
)

ORDER BY posts.id DESC
""", (session["user_id"], session["user_id"]))

    posts = cursor.fetchall()
    posts = [list(p) for p in posts]
    for p in posts:
        p[4] = time_ago(p[4])
    print(posts)

# Comments with username
    cursor.execute("""
SELECT
    comments.id,
    comments.post_id,
    comments.user_id,
    users.username,
    comments.comment,
    comments.created_at
FROM comments
JOIN users
ON comments.user_id = users.id
ORDER BY comments.id ASC
""")

    comments = cursor.fetchall()

# suggested users
    cursor.execute("""
SELECT id, username
FROM users
WHERE id != %s
AND id NOT IN (
    SELECT following_id FROM followers WHERE follower_id = %s
)
LIMIT 5
""", (session["user_id"], session["user_id"]))

    suggested_users = cursor.fetchall()

    conn.close()

    return render_template(
    "feed.html",
    username=session["username"],
    posts=posts,
    comments=comments,
    suggested_users=suggested_users
)


# =========================
# CREATE POST
# =========================
@app.route("/create_post", methods=["POST"])
def create_post():

    if "user_id" not in session:
        return redirect("/login")

    content = request.form.get("content", "")
    image = request.files.get("image")
    print("CONTENT:", content)
    print("IMAGE:", image)

    # =========================
    # CYBERBULLYING DETECTION
    # =========================
    prediction = classify_text(content)

    if prediction is not None:

        print("🔍 Post Prediction:", prediction)

        if str(prediction).lower() in ["toxic", "hatespeech"]:

            # ADDED: notify parent if this account belongs to a minor
            notify_parent_of_flagged_content(
                user_id=session["user_id"],
                content_type="post",
                category=str(prediction)
            )

            return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Cyberbullying Detected</title>
    <style>
        body {{
            margin: 0;
            padding: 0;
            font-family: Arial, sans-serif;
            background: #f4f6f8;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
        }}

        .warning-card {{
            width: 90%;
            max-width: 500px;
            background: white;
            border-radius: 18px;
            padding: 40px;
            text-align: center;
            box-shadow: 0 10px 30px rgba(0,0,0,0.15);
            border-top: 6px solid #e74c3c;
        }}

        .warning-icon {{
            font-size: 55px;
            margin-bottom: 10px;
        }}

        h1 {{
            color: #e74c3c;
            margin-bottom: 15px;
        }}

        .message {{
            color: #555;
            font-size: 17px;
            line-height: 1.6;
        }}

        .detected {{
            display: inline-block;
            margin: 15px 0;
            padding: 10px 22px;
            background: #fdecea;
            color: #c0392b;
            border-radius: 20px;
            font-weight: bold;
            text-transform: uppercase;
        }}

        .back-btn {{
            display: inline-block;
            margin-top: 20px;
            padding: 12px 25px;
            background: #e74c3c;
            color: white;
            text-decoration: none;
            border-radius: 8px;
            font-weight: bold;
        }}

        .back-btn:hover {{
            background: #c0392b;
        }}
    </style>
</head>

<body>

    <div class="warning-card">

        <div class="warning-icon">⚠️</div>

        <h1>Cyberbullying Detected</h1>

        <p class="message">
            Your post was detected as potentially harmful or abusive.
        </p>

        <div class="detected">
            {prediction}
        </div>

        <p class="message">
            Please revise your language before posting.
        </p>

        <a href="/feed" class="back-btn">
            ← Go Back
        </a>

    </div>

</body>
</html>
"""

    # =========================
    # IMAGE UPLOAD
    # =========================
    filename = None

    if image and image.filename != "":
        filename = secure_filename(image.filename)

        upload_path = os.path.join(
            app.config["UPLOAD_FOLDER"],
            filename
        )

        image.save(upload_path)

        print("✅ Image saved:", filename)

    # =========================
    # SAVE POST
    # =========================
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO posts(user_id, content, image)
        VALUES (%s, %s, %s)
    """,
    (
        session["user_id"],
        content,
        filename
    ))

    conn.commit()
    conn.close()

    return redirect("/feed")

# =========================
# LIKE / UNLIKE
# =========================
@app.route("/like/<int:post_id>")
def like(post_id):

    if "user_id" not in session:
        return jsonify({"success": False}), 401

    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM likes
        WHERE post_id=%s AND user_id=%s
    """, (post_id, session["user_id"]))

    existing = cursor.fetchone()

    if existing:
        cursor.execute("""
            DELETE FROM likes
            WHERE post_id=%s AND user_id=%s
        """, (post_id, session["user_id"]))
    else:
        cursor.execute("""
            INSERT INTO likes(post_id, user_id)
            VALUES (%s, %s)
        """, (post_id, session["user_id"]))

    conn.commit()

    cursor.execute("""
        SELECT COUNT(*)
        FROM likes
        WHERE post_id=%s
    """, (post_id,))

    likes = cursor.fetchone()[0]

    # only notify on a fresh like, not on unlike
    notify_recipient = None
    if not existing:
        cursor.execute("SELECT user_id FROM posts WHERE id=%s", (post_id,))
        post_owner_row = cursor.fetchone()
        if post_owner_row:
            notify_recipient = post_owner_row[0]

    conn.close()

    # create_notification opens its own connection, so it must run
    # only after the connection above is fully closed
    if notify_recipient is not None:
        create_notification(
            recipient_id=notify_recipient,
            actor_id=session["user_id"],
            type="like",
            target_type="post",
            target_id=post_id,
        )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
       return jsonify({
            "success": True,
            "likes": likes
        })

    return redirect(request.referrer or "/feed")



# =========================
# COMMENT
# =========================
@app.route("/comment", methods=["POST"])
def comment():

    print("===== COMMENT ROUTE HIT =====")
    print(request.form)

    if "user_id" not in session:
        return redirect("/login")

    post_id = request.form["post_id"]
    comment_text = request.form["comment"]

    print(post_id)
    print(comment_text)

    # =========================
    # CYBERBULLYING DETECTION
    # (fixed indentation bug from the original file — this whole block
    # is now correctly nested so it can never run with an undefined
    # `prediction` if the model failed to load)
    # =========================
    prediction = classify_text(comment_text)

    if prediction is not None:

        print("🔍 Comment Prediction:", prediction)

        if str(prediction).lower() in ["toxic", "hatespeech"]:

            # ADDED: notify parent if this account belongs to a minor
            notify_parent_of_flagged_content(
                user_id=session["user_id"],
                content_type="comment",
                category=str(prediction)
            )

            return f"""
<!DOCTYPE html>
<html>
<head>
<title>Content Moderation</title>

<style>
body {{
    margin: 0;
    padding: 0;
    background: linear-gradient(135deg, #1f2937, #111827);
    font-family: Arial, sans-serif;
    height: 100vh;
    display: flex;
    justify-content: center;
    align-items: center;
}}

.card {{
    background: #ffffff;
    width: 520px;
    padding: 35px;
    border-radius: 16px;
    text-align: center;
    box-shadow: 0 12px 35px rgba(0,0,0,0.25);
}}

.icon {{
    font-size: 55px;
    margin-bottom: 10px;
}}

h1 {{
    color: #111827;
    margin-bottom: 10px;
}}

.subtext {{
    color: #6b7280;
    font-size: 15px;
}}

.tag {{
    display: inline-block;
    margin-top: 18px;
    padding: 10px 18px;
    border-radius: 8px;
    background: #f3f4f6;
    color: #111827;
    font-weight: bold;
    letter-spacing: 1px;

}}


.btn {{
    display: inline-block;
    margin-top: 22px;
    padding: 12px 22px;
    background: #4f46e5;
    color: white;
    text-decoration: none;
    border-radius: 10px;
    transition: 0.2s;
}}

.btn:hover {{
    background: #4338ca;
}}
</style>

</head>

<body>

<div class="card">

    <div class="icon">🛡️</div>

    <h1>Content Blocked</h1>

    <p class="subtext">Our Cyberbullying Detection System detected inappropriate content.</p>

    <div class="tag {'toxic' if prediction.lower() == 'toxic' else 'hate'}">
        {prediction.upper()}
    </div>

    <br>

    <a href="/feed" class="btn">Return to Feed</a>

</div>

</body>
</html>
"""

    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO comments(post_id, user_id, comment)
        VALUES (%s, %s, %s)
    """, (post_id, session["user_id"], comment_text))

    conn.commit()

# Get updated comment count
    cursor.execute("""
SELECT COUNT(*)
FROM comments
WHERE post_id = %s
""", (post_id,))

    comment_count = cursor.fetchone()[0]

    # notify the post owner about the new comment
    cursor.execute("SELECT user_id FROM posts WHERE id=%s", (post_id,))
    post_owner_row = cursor.fetchone()
    notify_recipient = post_owner_row[0] if post_owner_row else None

    conn.close()

    # create_notification opens its own connection, so it must run
    # only after the connection above is fully closed
    if notify_recipient is not None:
        create_notification(
            recipient_id=notify_recipient,
            actor_id=session["user_id"],
            type="comment",
            target_type="post",
            target_id=post_id,
        )

# AJAX request
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
     return jsonify({
        "success": True,
            "comments": comment_count
    })

# Normal request
    return redirect(request.referrer or "/feed")

# =========================
# PROFILE
# =========================
@app.route("/profile/<username>")
def profile(username):

    # Login check
    if "user_id" not in session:
        return redirect("/login")


    conn = get_conn()
    cursor = conn.cursor()


    # Get profile user
    cursor.execute("""
        SELECT 
            id,
            username,
            email,
            bio,
            profile_image,
            cover_image,
            location,
            website
        FROM users
        WHERE username=%s
    """, (username,))


    profile_user = cursor.fetchone()



    if not profile_user:
        conn.close()
        return "User not found"



    profile_id = profile_user[0]



    # Followers count

    cursor.execute("""
        SELECT COUNT(*)
        FROM followers
        WHERE following_id=%s
    """, (profile_id,))


    followers_count = cursor.fetchone()[0]



    # Following count

    cursor.execute("""
        SELECT COUNT(*)
        FROM followers
        WHERE follower_id=%s
    """, (profile_id,))


    following_count = cursor.fetchone()[0]



    # Check if current user follows this user

    cursor.execute("""
        SELECT id
        FROM followers
        WHERE follower_id=%s
        AND following_id=%s
    """,
    (
        session["user_id"],
        profile_id
    ))


    is_following = cursor.fetchone() is not None





    # User posts (now also includes comment_count, like the feed)

    cursor.execute("""
        SELECT
            posts.id,
            posts.content,
            posts.created_at,
            COUNT(likes.id) AS likes,
            posts.image,
            (
                SELECT COUNT(*)
                FROM comments
                WHERE comments.post_id = posts.id
            ) AS comment_count
        FROM posts

        LEFT JOIN likes
        ON posts.id = likes.post_id

        WHERE posts.user_id=%s

        GROUP BY posts.id

        ORDER BY posts.created_at DESC

    """,
    (profile_id,))


    posts = cursor.fetchall()
    posts = [list(p) for p in posts]
    for p in posts:
        p[2] = time_ago(p[2])


    # Comments for this profile's posts (same shape as feed's comments)

    cursor.execute("""
        SELECT
            comments.id,
            comments.post_id,
            comments.user_id,
            users.username,
            comments.comment,
            comments.created_at
        FROM comments
        JOIN users
        ON comments.user_id = users.id
        WHERE comments.post_id IN (
            SELECT id FROM posts WHERE user_id=%s
        )
        ORDER BY comments.id ASC
    """, (profile_id,))

    comments = cursor.fetchall()


    conn.close()



    return render_template(
        "profile.html",
        profile_user=profile_user,
        posts=posts,
        comments=comments,
        followers_count=followers_count,
        following_count=following_count,
        is_following=is_following
    )
# =========================
# FOLLOW / UNFOLLOW
# =========================
@app.route("/follow/<int:user_id>")
def follow(user_id):

    if "user_id" not in session:
        return redirect("/login")

    if session["user_id"] == user_id:
        return redirect("/feed")

    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM followers
        WHERE follower_id=%s AND following_id=%s
    """, (session["user_id"], user_id))

    existing = cursor.fetchone()

    if not existing:
        cursor.execute("""
            INSERT INTO followers(follower_id, following_id)
            VALUES (%s, %s)
        """, (session["user_id"], user_id))
        conn.commit()
        conn.close()

        # notify the user being followed (opens its own connection,
        # so the one above must be committed and closed first)
        create_notification(
            recipient_id=user_id,
            actor_id=session["user_id"],
            type="follow",
            target_type="profile",
            target_id=session["user_id"],
        )
    else:
        cursor.execute("""
            DELETE FROM followers
            WHERE follower_id=%s AND following_id=%s
        """, (session["user_id"], user_id))
        conn.commit()
        conn.close()

    return redirect(request.referrer or "/feed")
# =========================
# DELETE POST
# =========================
@app.route("/delete_post/<int:post_id>")
def delete_post(post_id):

    if "user_id" not in session:
        return redirect("/login")

    conn = get_conn()
    cursor = conn.cursor()

    # check post owner
    cursor.execute("""
        SELECT user_id FROM posts WHERE id=%s
    """, (post_id,))

    post = cursor.fetchone()

    if post and post[0] == session["user_id"]:

        # delete related data first
        cursor.execute("DELETE FROM comments WHERE post_id=%s", (post_id,))
        cursor.execute("DELETE FROM likes WHERE post_id=%s", (post_id,))
        cursor.execute("DELETE FROM posts WHERE id=%s", (post_id,))

        conn.commit()

    conn.close()

    return redirect("/feed")
@app.route("/delete_comment/<int:comment_id>")
def delete_comment(comment_id):

    if "user_id" not in session:
        return redirect("/login")

    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM comments
        WHERE id=%s AND user_id=%s
    """, (comment_id, session["user_id"]))

    conn.commit()
    conn.close()

    return redirect("/feed")


# =========================
# SEARCH USERS
# =========================
@app.route("/search")
def search():

    if "user_id" not in session:
        return redirect("/login")

    query = request.args.get("q", "")

    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, username
        FROM users
        WHERE username LIKE %s
    """, ('%' + query + '%',))

    users = cursor.fetchall()
    conn.close()

    return render_template("search.html", users=users, query=query)


# =========================
# LOGOUT
# =========================
@app.route("/logout")
def logout():

    session.clear()
    return redirect("/")


# =========================
# DEBUG
# =========================
@app.route("/test")
def test():
    return "Flask is working"


@app.before_request
def before_request():
    print("➡ Request:", request.method, request.path)

@app.route("/db")
def db_page():
    conn = get_conn()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cursor.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='public'
    """)
    tables = cursor.fetchall()

    db_data = {}

    for table in tables:
        table_name = table["table_name"]

        cursor.execute(f"SELECT * FROM {table_name} LIMIT 20")
        rows = cursor.fetchall()

        db_data[table_name] = rows

    conn.close()

    return render_template("db.html", db_data=db_data)


# =========================
# NOTIFICATIONS: routes + socket events
# =========================
@socketio.on("connect")
def handle_connect():
    if "user_id" in session:
        join_room(str(session["user_id"]))


@app.route("/notifications")
def get_notifications():
    if "user_id" not in session:
        return jsonify({"success": False}), 401

    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            notifications.id,
            notifications.type,
            notifications.target_type,
            notifications.target_id,
            notifications.is_read,
            notifications.created_at,
            users.username
        FROM notifications
        JOIN users ON users.id = notifications.actor_id
        WHERE notifications.recipient_id = %s
        ORDER BY notifications.created_at DESC
        LIMIT 30
    """, (session["user_id"],))

    rows = cursor.fetchall()
    conn.close()

    notifications = []
    for row in rows:
        notifications.append({
            "id": row[0],
            "type": row[1],
            "target_type": row[2],
            "target_id": row[3],
            "is_read": bool(row[4]),
            "created_at": time_ago(row[5]),
            "actor_username": row[6],
        })

    return jsonify({"success": True, "notifications": notifications})


@app.route("/notifications/unread_count")
def unread_count():
    if "user_id" not in session:
        return jsonify({"success": False}), 401

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) FROM notifications
        WHERE recipient_id=%s AND is_read=0
    """, (session["user_id"],))
    count = cursor.fetchone()[0]
    conn.close()

    return jsonify({"success": True, "count": count})


@app.route("/notifications/mark_read", methods=["POST"])
def mark_read():
    if "user_id" not in session:
        return jsonify({"success": False}), 401

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE notifications SET is_read=1
        WHERE recipient_id=%s
    """, (session["user_id"],))
    conn.commit()
    conn.close()

    return jsonify({"success": True})


# =========================
# RUN
# =========================
# Render sets the PORT environment variable dynamically — binding to a
# hardcoded 5000 would fail in production, so this falls back to 5000
# only for local development.
#
# debug=False here because Flask-SocketIO's dev server refuses to run
# with debug=True outside your own machine (it assumes debug mode means
# "not production" and blocks it as a safety measure — see the
# allow_unsafe_werkzeug flag below, which explicitly opts back in since
# this is a small free-tier deployment, not a high-traffic production
# service).
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
