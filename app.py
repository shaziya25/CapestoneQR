from flask import Flask, render_template, request, redirect, session, make_response, Response, url_for
import csv, uuid, qrcode, os, math
from datetime import datetime, timedelta
from io import StringIO

app = Flask(__name__)
app.secret_key = "secret123"

ADMIN_FILE = "admin.csv"
SESSION_FILE = "sessions.csv"
ATT_FILE = "attendance.csv"

MAX_DISTANCE = 80  # meters


# ---------- Distance (Haversine) ----------
def distance_meters(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


# ---------- REGISTER ----------
@app.route("/", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        admin_id = request.form["admin_id"].strip()
        password = request.form["password"].strip()
        if admin_id and password:
            with open(ADMIN_FILE, "a", newline="") as f:
                csv.writer(f).writerow([admin_id, password])
            return redirect("/login")
    return render_template("register.html")


# ---------- LOGIN ----------
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
    return render_template("login.html")


# ---------- DASHBOARD ----------
@app.route("/dashboard")
def dashboard():
    if "admin" not in session:
        return redirect("/login")

    sessions, records = [], []
    selected_session_id = request.args.get("session_id")

    try:
        with open(SESSION_FILE) as f:
            for r in csv.reader(f):
                if r and r[5] == session["admin"]:
                    sessions.append(r)
    except FileNotFoundError:
        pass

    session_ids = {s[0] for s in sessions}

    try:
        with open(ATT_FILE) as f:
            for r in csv.reader(f):
                if r and r[0] in session_ids:
                    if not selected_session_id or r[0] == selected_session_id:
                        records.append(r)
    except FileNotFoundError:
        pass

    qr_data = dict(session_id=None, subject=None, class_id=None, expiry=None)

    if sessions:
        chosen = next((s for s in sessions if s[0] == selected_session_id), sessions[-1])
        qr_data = {
            "session_id": chosen[0],
            "subject": chosen[1],
            "class_id": chosen[2],
            "expiry": chosen[4]
        }

    return render_template("dashboard.html", sessions=sessions, records=records, **qr_data)


# ---------- GENERATE QR ----------
@app.route("/generate", methods=["POST"])
def generate():
    if "admin" not in session:
        return redirect("/login")

    subject = request.form.get("subject")
    class_id = request.form.get("class_id")
    duration = int(request.form.get("duration", 0))
    lat = request.form.get("latitude")
    lon = request.form.get("longitude")

    if not lat or not lon:
        return redirect(url_for("dashboard"))

    sid = str(uuid.uuid4())[:8]
    start = datetime.now()
    expiry = start + timedelta(minutes=duration)

    with open(SESSION_FILE, "a", newline="") as f:
        csv.writer(f).writerow([
            sid, subject, class_id,
            start.strftime("%Y-%m-%d %H:%M:%S"),
            expiry.strftime("%Y-%m-%d %H:%M:%S"),
            session["admin"], "ON", lat, lon
        ])

    url = request.host_url + f"attendance/{sid}"
    os.makedirs("static", exist_ok=True)
    qrcode.make(url).save("static/qr.png")

    return redirect(url_for("dashboard", session_id=sid))


# ---------- ATTENDANCE ----------
@app.route("/attendance/<sid>", methods=["GET", "POST"])
def attendance(sid):
    session_data = None
    try:
        with open(SESSION_FILE) as f:
            for r in csv.reader(f):
                if r and r[0] == sid:
                    session_data = r
                    break
    except FileNotFoundError:
        pass

    if not session_data:
        return "Invalid session"

    subject, class_id = session_data[1], session_data[2]
    session_lat, session_lon = float(session_data[7]), float(session_data[8])

    device_cookie = request.cookies.get("device_id") or str(uuid.uuid4())

    if request.method == "POST":
        name = request.form["name"]
        roll = request.form["roll"]
        lat = float(request.form["latitude"])
        lon = float(request.form["longitude"])

        dist = distance_meters(session_lat, session_lon, lat, lon)
        if dist > MAX_DISTANCE:
            return render_template("mark_attendance.html", subject=subject, class_id=class_id,
                                   error="You are too far from the class")

        with open(ATT_FILE, "a", newline="") as f:
            csv.writer(f).writerow([
                sid, subject, class_id, roll, name,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                device_cookie, round(dist, 2)
            ])

        resp = make_response(render_template("student_result.html",
                                             student_name=name, class_id=class_id,
                                             total_classes=1, attended=1,
                                             missed=0, percent=100,
                                             status="Good Standing"))
        resp.set_cookie("device_id", device_cookie, max_age=60 * 60 * 24 * 30)
        return resp

    resp = make_response(render_template("mark_attendance.html",
                                         subject=subject, class_id=class_id))
    resp.set_cookie("device_id", device_cookie, max_age=60 * 60 * 24 * 30)
    return resp


# ---------- LOGOUT ----------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


if __name__ == "__main__":
    app.run(debug=True)
