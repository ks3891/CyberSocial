from flask import Flask, render_template, request, redirect, session
import sqlite3
import os
from werkzeug.utils import secure_filename
# =========================
# ADDED FOR CYBERBULLYING DETECTION
# =========================
import joblib

app = Flask(__name__)
app.secret_key = "cybersocial_secret_key"
# Upload folder
UPLOAD_FOLDER = "static/uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
# =========================
# CYBERBULLYING MODEL
# =========================
try:
    model = joblib.load("cyberbullying_model.pkl")
    vectorizer = joblib.load("vectorizer.pkl")
    print("✅ Cyberbullying model loaded")
except Exception as e:
    print("❌ Model loading failed:", e)
    model = None
    vectorizer = None


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

        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()

        # check username
        cursor.execute("SELECT * FROM users WHERE username=?", (username,))
        if cursor.fetchone():
            conn.close()
            return "Username already exists"

        # check email
        cursor.execute("SELECT * FROM users WHERE email=?", (email,))
        if cursor.fetchone():
            conn.close()
            return "Email already exists"

        # insert user
        cursor.execute("""
            INSERT INTO users(username, email, password)
            VALUES (?, ?, ?)
        """, (username, email, password))

        conn.commit()
        conn.close()

        return redirect("/login")

    return render_template("signup.html")


# =========================
# LOGIN
# =========================
@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form["email"]
        password = request.form["password"]

        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM users
            WHERE email=? AND password=?
        """, (email, password))

        user = cursor.fetchone()
        conn.close()

        if user:
            session["user_id"] = user[0]
            session["username"] = user[1]
            return redirect("/feed")

        return "Invalid credentials"

    return render_template("login.html")


# =========================
# FEED
# =========================
@app.route("/feed")
def feed():

    if "user_id" not in session:
        return redirect("/login")

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    # posts (feed)
    cursor.execute("""
SELECT
    users.username,
    posts.content,
    posts.id,
    (
        SELECT COUNT(*) FROM likes
        WHERE post_id = posts.id
    ) AS likes,
    posts.created_at,
    posts.image
FROM posts
JOIN users ON posts.user_id = users.id
WHERE posts.user_id = ?
OR posts.user_id IN (
    SELECT following_id
    FROM followers
    WHERE follower_id = ?
)
ORDER BY posts.id DESC
""", (session["user_id"], session["user_id"]))

    posts = cursor.fetchall()

    ## comments
    cursor.execute(""" SELECT id, post_id, user_id, comment FROM comments ORDER BY id ASC """) 
    comments = cursor.fetchall()
    # suggested users
    cursor.execute("""
    SELECT id, username
    FROM users
    WHERE id != ?
    AND id NOT IN (
        SELECT following_id FROM followers WHERE follower_id = ?
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
    if model is not None and vectorizer is not None:

        text_vec = vectorizer.transform([content])
        prediction = model.predict(text_vec)[0]

        print("🔍 Post Prediction:", prediction)

        if str(prediction).lower() in ["toxic", "hatespeech"]:

            return f"""
            <h2>⚠️ Post Blocked</h2>
            <p>Your post was detected as:</p>
            <h3>{prediction}</h3>
            <br>
            <a href='/feed'>⬅ Go Back</a>
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
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO posts(user_id, content, image)
        VALUES (?, ?, ?)
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
        return redirect("/login")

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM likes
        WHERE post_id=? AND user_id=?
    """, (post_id, session["user_id"]))

    existing = cursor.fetchone()

    if not existing:
        cursor.execute("""
            INSERT INTO likes(post_id, user_id)
            VALUES (?, ?)
        """, (post_id, session["user_id"]))
    else:
        cursor.execute("""
            DELETE FROM likes
            WHERE post_id=? AND user_id=?
        """, (post_id, session["user_id"]))

    conn.commit()
    conn.close()

    return redirect("/feed")


# =========================
# COMMENT
# =========================
# =========================
# COMMENT
# =========================
@app.route("/comment", methods=["POST"])
def comment():

    if "user_id" not in session:
        return redirect("/login")

    post_id = request.form["post_id"]
    comment_text = request.form["comment"]

    # =========================
    # CYBERBULLYING DETECTION
    # =========================
    if model is not None and vectorizer is not None:

        text_vec = vectorizer.transform([comment_text])
        prediction = model.predict(text_vec)[0]

        print("🔍 Comment Prediction:", prediction)

        if str(prediction).lower() in ["toxic", "hatespeech"]:

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

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO comments(post_id, user_id, comment)
        VALUES (?, ?, ?)
    """, (post_id, session["user_id"], comment_text))

    conn.commit()
    conn.close()

    return redirect("/feed")

# =========================
# PROFILE
# =========================
@app.route("/profile/<username>")
def profile(username):

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, username
        FROM users
        WHERE username=?
    """, (username,))

    user = cursor.fetchone()

    if not user:
        conn.close()
        return "User not found"

    user_id = user[0]

    # User posts
    cursor.execute("""
    SELECT content, image, created_at, id
    FROM posts
    WHERE user_id=?
    ORDER BY id DESC
""", (user_id,))
    posts = cursor.fetchall()

    # Followers count
    cursor.execute("""
        SELECT COUNT(*)
        FROM followers
        WHERE following_id=?
    """, (user_id,))
    followers_count = cursor.fetchone()[0]

    # Following count
    cursor.execute("""
        SELECT COUNT(*)
        FROM followers
        WHERE follower_id=?
    """, (user_id,))
    following_count = cursor.fetchone()[0]

    # Is current user following this profile?
    is_following = False

    if "user_id" in session:
        cursor.execute("""
            SELECT *
            FROM followers
            WHERE follower_id=? AND following_id=?
        """, (session["user_id"], user_id))

        is_following = cursor.fetchone() is not None

    conn.close()

    return render_template(
        "profile.html",
        user=user,
        posts=posts,
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

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM followers
        WHERE follower_id=? AND following_id=?
    """, (session["user_id"], user_id))

    existing = cursor.fetchone()

    if not existing:
        cursor.execute("""
            INSERT INTO followers(follower_id, following_id)
            VALUES (?, ?)
        """, (session["user_id"], user_id))
    else:
        cursor.execute("""
            DELETE FROM followers
            WHERE follower_id=? AND following_id=?
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

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    # check post owner
    cursor.execute("""
        SELECT user_id FROM posts WHERE id=?
    """, (post_id,))

    post = cursor.fetchone()

    if post and post[0] == session["user_id"]:

        # delete related data first
        cursor.execute("DELETE FROM comments WHERE post_id=?", (post_id,))
        cursor.execute("DELETE FROM likes WHERE post_id=?", (post_id,))
        cursor.execute("DELETE FROM posts WHERE id=?", (post_id,))

        conn.commit()

    conn.close()

    return redirect("/feed")
@app.route("/delete_comment/<int:comment_id>")
def delete_comment(comment_id):

    if "user_id" not in session:
        return redirect("/login")

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM comments
        WHERE id=? AND user_id=?
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

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, username
        FROM users
        WHERE username LIKE ?
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
    import sqlite3

    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row   # ⭐ IMPORTANT FIX
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()

    db_data = {}

    for table in tables:
        table_name = table["name"]

        cursor.execute(f"SELECT * FROM {table_name} LIMIT 20")
        rows = cursor.fetchall()

        db_data[table_name] = rows

    conn.close()

    return render_template("db.html", db_data=db_data)


# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)