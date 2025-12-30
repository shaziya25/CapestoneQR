from flask import Flask, render_template, request, redirect, session, send_file, make_response
import csv, uuid, qrcode
from datetime import datetime, timedelta
import os

app = Flask(__name__)
app.secret_key = "secret123"

ADMIN_FILE = "admin.csv"
SESSION_FILE = "sessions.csv"
ATT_FILE = "attendance.csv"

@app.after_request
def add_ngrok_header(response):
    response.headers["ngrok-skip-browser-warning"] = "true"
    return response

# ---------------- REGISTER ----------------
@app.route("/", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        admin_id = request.form["admin_id"].strip()
        password = request.form["password"].strip()
        if admin_id and password:
            with open(ADMIN_FILE, "a", newline="") as f:
                csv.writer(f).writerow([admin_id, password])
            return redirect("/login")
        return render_template("register.html", error="All fields required")
    return render_template("register.html")

# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        admin_id = request.form["admin_id"].strip()
        password = request.form["password"].strip()
        try:
            with open(ADMIN_FILE) as f:
                for r in csv.reader(f):
                    if r and r[0] == admin_id and r[1] == password:
                        session["admin"] = admin_id
                        return redirect("/dashboard")
        except FileNotFoundError:
            pass
        return render_template("login.html", error="Invalid credentials")
    return render_template("login.html")

# ---------------- DASHBOARD ----------------
@app.route("/dashboard")
def dashboard():
    if "admin" not in session:
        return redirect("/login")

    admin_id = session["admin"]
    selected_session_id = request.args.get("session_id")

    sessions = []
    try:
        with open(SESSION_FILE) as f:
            for r in csv.reader(f):
                if r and len(r) >= 6 and r[5] == admin_id:
                    if len(r) < 7:
                        r.append("ON")  # default lock
                    sessions.append(r)
    except FileNotFoundError:
        pass

    session_ids = {s[0] for s in sessions}

    records = []
    try:
        with open(ATT_FILE) as f:
            for r in csv.reader(f):
                if r and r[0] in session_ids:
                    if not selected_session_id or r[0] == selected_session_id:
                        records.append(r)
    except FileNotFoundError:
        pass

    qr_data = {"session_id": None, "subject": None, "class_id": None, "expiry": None, "device_lock": None}

    if sessions:
        chosen = next((s for s in sessions if s[0] == selected_session_id), sessions[-1])
        qr_data = {
            "session_id": chosen[0],
            "subject": chosen[1],
            "class_id": chosen[2],
            "expiry": chosen[4],
            "device_lock": chosen[6]
        }

    return render_template("dashboard.html", sessions=sessions, records=records, **qr_data)

# ---------------- TOGGLE DEVICE LOCK ----------------
@app.route("/toggle-lock/<sid>")
def toggle_lock(sid):
    if "admin" not in session:
        return redirect("/login")

    rows = []
    with open(SESSION_FILE) as f:
        for r in csv.reader(f):
            if r and r[0] == sid and r[5] == session["admin"]:
                if len(r) < 7:
                    r.append("ON")
                r[6] = "OFF" if r[6] == "ON" else "ON"
            rows.append(r)

    with open(SESSION_FILE, "w", newline="") as f:
        csv.writer(f).writerows(rows)

    return redirect("/dashboard")

# ---------------- GENERATE QR ----------------
@app.route("/generate", methods=["POST"])
def generate():
    if "admin" not in session:
        return redirect("/login")

    subject = request.form["subject"].strip()
    class_id = request.form["class_id"].strip()
    duration = int(request.form["duration"])

    sid = str(uuid.uuid4())[:8]
    start = datetime.now()
    expiry = start + timedelta(minutes=duration)

    with open(SESSION_FILE, "a", newline="") as f:
        csv.writer(f).writerow([
            sid, subject, class_id,
            start.strftime("%Y-%m-%d %H:%M:%S"),
            expiry.strftime("%Y-%m-%d %H:%M:%S"),
            session["admin"],
            "ON"
        ])

    url = request.host_url + f"attendance/{sid}"
    os.makedirs("static", exist_ok=True)
    qrcode.make(url).save("static/qr.png")

    return redirect("/dashboard")

# ---------------- ATTENDANCE ----------------
@app.route("/attendance/<sid>", methods=["GET", "POST"])
def attendance(sid):
    session_data = None
    try:
        with open(SESSION_FILE) as f:
            for r in csv.reader(f):
                if r and r[0] == sid:
                    if len(r) < 7:
                        r.append("ON")
                    session_data = r
                    break
    except FileNotFoundError:
        pass

    if not session_data:
        return "Invalid session", 404

    subject, class_id = session_data[1], session_data[2]
    expiry = datetime.strptime(session_data[4], "%Y-%m-%d %H:%M:%S")
    device_lock = session_data[6]

    if datetime.now() > expiry:
        return "QR expired", 403

    device_cookie = request.cookies.get("device_id") or str(uuid.uuid4())

    if request.method == "POST":
        roll = request.form["roll"].strip()
        name = request.form["name"].strip()

        try:
            with open(ATT_FILE) as f:
                for r in csv.reader(f):
                    # Check if device lock ON and same device already marked attendance
                    if device_lock == "ON" and r and len(r) >= 7 and r[0] == sid and r[6] == device_cookie:
                        return render_template("mark_attendance.html",
                                               subject=subject, class_id=class_id,
                                               error="Attendance already marked from this device")
                    # Check if same roll already marked attendance for this session
                    if r and r[0] == sid and r[3] == roll:
                        return render_template("mark_attendance.html",
                                               subject=subject, class_id=class_id,
                                               error="Attendance already marked for this roll number")
        except FileNotFoundError:
            pass

        with open(ATT_FILE, "a", newline="") as f:
            csv.writer(f).writerow([
                sid, subject, class_id,
                roll, name,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                device_cookie
            ])

        resp = make_response(render_template("student_result.html",
                                             student_name=name, class_id=class_id,
                                             total_classes=1, attended=1, missed=0,
                                             percent=100, status="Good Standing"))
        resp.set_cookie("device_id", device_cookie, max_age=60 * 60 * 24 * 30)
        return resp

    resp = make_response(render_template("mark_attendance.html", subject=subject, class_id=class_id))
    resp.set_cookie("device_id", device_cookie, max_age=60 * 60 * 24 * 30)
    return resp

# ---------------- RECORDS ----------------
@app.route("/records", methods=["GET", "POST"])
def records():
    if "admin" not in session:
        return redirect("/login")

    admin_id = session["admin"]
    search_query = ""

    if request.method == "POST":
        search_query = request.form.get("search", "").strip().lower()

    admin_sessions = set()
    try:
        with open(SESSION_FILE) as f:
            for row in csv.reader(f):
                if row and len(row) >= 6 and row[5] == admin_id:
                    admin_sessions.add(row[0])
    except FileNotFoundError:
        pass

    records = []
    try:
        with open(ATT_FILE) as f:
            for row in csv.reader(f):
                if not row or len(row) < 6:
                    continue
                if row[0] not in admin_sessions:
                    continue
                if search_query and search_query not in row[3].lower() and search_query not in row[4].lower():
                    continue
                records.append({
                    "session_id": row[0],
                    "subject": row[1],
                    "class_id": row[2],
                    "roll": row[3],
                    "name": row[4],
                    "timestamp": row[5]
                })
    except FileNotFoundError:
        pass

    return render_template("records.html", records=records, search_query=search_query)

# ---------------- DOWNLOAD ----------------
@app.route("/download")
def download():
    if "admin" not in session:
        return redirect("/login")
    return send_file(ATT_FILE, as_attachment=True)

# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
