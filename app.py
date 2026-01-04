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
                        r.append("ON")
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
                if r and len(r) >= 5 and r[0] == sid:
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

        # --------- Prevent duplicate attendance ---------
        try:
            with open(ATT_FILE) as f:
                for r in csv.reader(f):
                    if not r or len(r) < 5:
                        continue
                    # Prevent duplicate roll for this session
                    if r[0] == sid and r[3] == roll:
                        return render_template("mark_attendance.html",
                                               subject=subject,
                                               class_id=class_id,
                                               error="Attendance already marked for this roll number")
                    # Device lock check
                    if device_lock == "ON" and len(r) >= 7 and r[6] == device_cookie and r[0] == sid:
                        return render_template("mark_attendance.html",
                                               subject=subject,
                                               class_id=class_id,
                                               error="Attendance already marked from this device")
        except FileNotFoundError:
            pass

        # Save attendance
        with open(ATT_FILE, "a", newline="") as f:
            csv.writer(f).writerow([
                sid, subject, class_id,
                roll, name,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                device_cookie
            ])

        # Subject-wise attendance calculation
        total_conducted = 0
        attended = 0
        try:
            with open(SESSION_FILE) as f:
                for s in csv.reader(f):
                    if s and len(s) >= 3 and s[1] == subject and s[2] == class_id:
                        total_conducted += 1
            with open(ATT_FILE) as f:
                for r in csv.reader(f):
                    if r and len(r) >= 5 and r[1] == subject and r[2] == class_id and r[4] == name:
                        attended += 1
        except FileNotFoundError:
            pass

        missed = total_conducted - attended
        percent = round((attended / total_conducted * 100) if total_conducted else 0, 2)
        warning = percent < 75

        resp = make_response(render_template(
            "student_result.html",
            student_name=name,
            class_id=class_id,
            subject=subject,
            total_classes=total_conducted,
            attended=attended,
            missed=missed,
            percent=percent,
            warning=warning
        ))
        resp.set_cookie("device_id", device_cookie, max_age=60*60*24*30)
        return resp

    resp = make_response(render_template("mark_attendance.html",
                                         subject=subject,
                                         class_id=class_id))
    resp.set_cookie("device_id", device_cookie, max_age=60*60*24*30)
    return resp

# ---------------- RECORDS ----------------
@app.route("/records", methods=["GET", "POST"])
def records():
    if "admin" not in session:
        return redirect("/login")

    admin_id = session["admin"]
    selected_session_id = request.form.get("session_id") or request.args.get("session_id")
    search_query = request.form.get("search", "").strip().lower()

    sessions = []
    try:
        with open(SESSION_FILE) as f:
            for r in csv.reader(f):
                if r and len(r) >= 6 and r[5] == admin_id:
                    sessions.append(r)
    except FileNotFoundError:
        pass

    records = []
    if selected_session_id:
        try:
            with open(ATT_FILE) as f:
                for r in csv.reader(f):
                    if not r or len(r) < 6:
                        continue
                    if r[0] != selected_session_id:
                        continue
                    if search_query and search_query not in r[3].lower() and search_query not in r[4].lower():
                        continue
                    records.append(r)
        except FileNotFoundError:
            pass

    return render_template(
        "records.html",
        sessions=sessions,
        records=records,
        selected_session_id=selected_session_id,
        search=search_query
    )

# ---------------- DOWNLOAD ----------------
@app.route("/download")
def download():
    if "admin" not in session:
        return redirect("/login")

    session_id = request.args.get("session_id")
    if not session_id:
        return redirect("/records")

    output = []
    output.append(["session_id", "subject", "class_id", "roll", "name", "timestamp"])

    try:
        with open(ATT_FILE) as f:
            for r in csv.reader(f):
                if r and r[0] == session_id:
                    output.append(r[:6])
    except FileNotFoundError:
        pass

    response = make_response("\n".join([",".join(row) for row in output]))
    response.headers["Content-Disposition"] = f"attachment; filename=attendance_{session_id}.csv"
    response.headers["Content-Type"] = "text/csv"
    return response

# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
