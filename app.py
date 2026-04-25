import os
import torch
from PIL import Image
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from transformers import BlipProcessor, BlipForConditionalGeneration
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, request, redirect, url_for, session
import requests
from flask import send_file
from io import BytesIO
# import google.generativeai as genai
import base64
from PIL import Image as PILImage
import io



# ------------------------------------------------
# FLASK APP SETUP
# ------------------------------------------------
app = Flask(__name__)
app.secret_key = "secret123"

UPLOAD_FOLDER = "static/uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# Load model once
processor = BlipProcessor.from_pretrained("Salesforce/blip-image-captioning-base")
model = BlipForConditionalGeneration.from_pretrained("Salesforce/blip-image-captioning-base")

# ------------------------------------------------
# SINGLE DATABASE → caption.db
# ------------------------------------------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'caption.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ------------------------------------------------
# DATABASE MODELS
# ------------------------------------------------

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

class ImageCaption(db.Model):
    __tablename__ = "image_caption"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # ADD THIS
    image_path = db.Column(db.String(200), nullable=False)
    tone = db.Column(db.String(50), nullable=False)
    language = db.Column(db.String(50), nullable=False)
    captions = db.Column(db.Text, nullable=False)
# class ImageCaption(db.Model):
#     __tablename__ = "image_caption"
#     id = db.Column(db.Integer, primary_key=True)
#     image_path = db.Column(db.String(200), nullable=False)
#     tone = db.Column(db.String(50), nullable=False)
#     language = db.Column(db.String(50), nullable=False)
#     captions = db.Column(db.Text, nullable=False)  # stored as text

# Create all tables once
with app.app_context():
    db.create_all()

# ------------------------------------------------
# SIGNUP
# ------------------------------------------------
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not username or not email or not password or not confirm_password:
            return "All fields are required"

        if password != confirm_password:
            return "Passwords do not match"

        if User.query.filter((User.username == username) | (User.email == email)).first():
            return "Username or Email already exists"

        hashed_password = generate_password_hash(password)

        new_user = User(username=username, email=email, password=hashed_password)
        db.session.add(new_user)
        db.session.commit()

        return redirect(url_for('login'))

    return render_template('signup.html')

# ------------------------------------------------
# LOGIN
# ------------------------------------------------
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
            session['user'] = user.username
            return redirect(url_for('home'))
        else:
            return "Invalid username or password"

    return render_template('login.html')

# ------------------------------------------------
# HOME (MAIN CAPTION PAGE) → STORES IN caption.db
# ------------------------------------------------
from googletrans import Translator
translator = Translator()

@app.route('/home', methods=['GET', 'POST'])
def home():
    if 'user' not in session:
        return redirect(url_for('login'))

    captions = []
    image_url = None

    if request.method == 'POST':
        file = request.files.get('image')
        tone = request.form.get("tone")
        language = request.form.get("language")

        if file and file.filename != "":
            os.makedirs("static/uploads", exist_ok=True)

            filename = secure_filename(file.filename)

            db_path = f"uploads/{filename}"          # what we store in DB
            full_path = f"static/uploads/{filename}" # where file is actually saved

            file.save(full_path)   
            image_url = db_path    

            # AI IMAGE CAPTIONING (always generate in English first)
            image = Image.open(full_path).convert("RGB")
            inputs = processor(image, return_tensors="pt")

            outputs = model.generate(
                **inputs,
                num_beams=10,
                num_return_sequences=7, 
                max_length=40
            )

            captions = [
                processor.decode(out, skip_special_tokens=True)
                for out in outputs
            ]

            # -------------------------------
            #  SMART CAPTION SELECTION
            # (more detail → more captions)
            # -------------------------------

            # Measure "information level" by caption length
            caption_lengths = [len(cap.split()) for cap in captions]

            avg_length = sum(caption_lengths) / len(caption_lengths)

            # Decide how many captions to keep
            if avg_length > 12:          # complex image
                final_count = 5
            elif avg_length > 8:         # medium complexity
                final_count = 4
            else:                        # simple image
                final_count = 3

            # Keep only top captions
            captions = captions[:final_count]


            # ------------------------------------------------
            #  EXTRA FILTER FOR FUNNY TONE (VERY IMPORTANT)
            # ------------------------------------------------
            if tone == "Fun":
                if avg_length <= 8:        # very simple image
                    final_count = 1
                elif avg_length <= 12:     # medium image
                    final_count = 2
                else:                      # complex image
                    final_count = 3

                captions = captions[:final_count]


            # -------------------------------
            #  APPLY TONE (Professional)
            # -------------------------------
            if tone == "Professional":
                captions = [
                    f"A professionally composed image showing {cap}"
                    for cap in captions
                ]
            elif tone == "Fun":
                funny_templates = [
                    "📸 {cap} — Main character energy unlocked!",
                    "🌊 Beach hair, don’t care! {cap}... and the vibes are immaculate!",
                    "😂 {cap} — Looks like this subject signed up for a photoshoot but forgot the script!"
                ]

                captions = [
                    funny_templates[i % len(funny_templates)].format(cap=cap)
                    for i, cap in enumerate(captions)
                ]


            # -------------------------------
            #  LANGUAGE HANDLING (Hindi & Gujarati WORK SAME)
            # -------------------------------
            if language == "Hindi":
                captions = [translator.translate(cap, dest="hi").text for cap in captions]

            elif language == "Gujarati":
                captions = [translator.translate(cap, dest="gu").text for cap in captions]

            captions_text = " | ".join(captions)

            
            # new_entry = ImageCaption(
            #     image_path=image_url,
            #     tone=tone,
            #     language=language,
            #     captions=captions_text
            # )
            user_obj = User.query.filter_by(username=session['user']).first()
            new_entry = ImageCaption(
            user_id=user_obj.id,   # ADD THIS LINE
            image_path=image_url,
            tone=tone,
            language=language,
            captions=captions_text
            )
            db.session.add(new_entry)
            db.session.commit()



    return render_template(
        'index.html',
        user=session['user'],
        captions=captions,
        image=image_url
    )

UNSPLASH_ACCESS_KEY = "GEqwYehHepY45fxn8DfscLuCDel4zl40KBc6YtiCDLU"

# genai.configure(api_key="AIzaSyBkKuMW8Dnq3Ra0ENfGvcPG983i2xWHTUI")
# Add these imports at the top of app.py
import requests as http_req
import urllib.request
import json

def get_place_history(place_name):
    try:
        query = place_name.replace(' ', '+')
        api_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={query}&format=json&srlimit=1"
        
        req_obj = urllib.request.Request(api_url, headers={'User-Agent': 'CaptionAI/1.0'})
        with urllib.request.urlopen(req_obj, timeout=10) as response:
            data = json.loads(response.read().decode())
        
        results = data.get("query", {}).get("search", [])
        if not results:
            return f"❌ Not found: '{place_name}'"
        
        title = results[0]["title"]
        title_encoded = title.replace(' ', '+')
        
        extract_url = f"https://en.wikipedia.org/w/api.php?action=query&titles={title_encoded}&prop=extracts&exintro=true&explaintext=true&redirects=1&format=json"
        req_obj2 = urllib.request.Request(extract_url, headers={'User-Agent': 'CaptionAI/1.0'})
        with urllib.request.urlopen(req_obj2, timeout=10) as response2:
            edata = json.loads(response2.read().decode())
        
        pages = edata.get("query", {}).get("pages", {})
        page = next(iter(pages.values()))
        extract = page.get("extract", "").strip()
        title = page.get("title", place_name)
        
        if extract:
            return f"🏛️ {title}\n\n📖 History & Overview:\n\n{extract}\n\n🌍 Source: Wikipedia"
        
        return "❌ No content found."
    
    except Exception as e:
        return f"❌ Error: {str(e)}"


def get_place_photos(place_name):
    """Fetch 3 photos from Unsplash."""
    try:
        r = http_req.get(
            "https://api.unsplash.com/search/photos",
            params={"query": place_name, "per_page": 3, "orientation": "landscape",
                    "client_id": UNSPLASH_ACCESS_KEY},
            timeout=8
        )
        if r.status_code == 200:
            return [{"url": p['urls']['regular'], "credit": p['user']['name']}
                    for p in r.json().get('results', [])]
    except:
        pass
    return []


@app.route('/image-to-text', methods=['GET', 'POST'])
def image_to_text():
    if 'user' not in session:
        return redirect(url_for('login'))

    history_info = None
    photos = []
    place = None
    error = None

    if request.method == 'POST':
        place = request.form.get('place', '').strip()
        if place:
            history_info = get_place_history(place)
            photos = get_place_photos(place)

    return render_template("image_to_text.html",
                           history_info=history_info,
                           photos=photos,
                           place=place,
                           error=error)

# ------------------------------------------------
# IMAGE SEARCH (INTERNET BASED - NO STATIC)
# ------------------------------------------------

@app.route("/image-search", methods=["GET", "POST"])
def image_search():
    images = []
    query = ""

    if request.method == "POST":
        query = request.form.get("search")

        url = "https://api.unsplash.com/search/photos"

        params = {
            "query": query,
            "per_page": 5,   # 👈 This gives 5 images
            "client_id": UNSPLASH_ACCESS_KEY
        }

        response = requests.get(url, params=params)
        data = response.json()

        if "results" in data:
            for result in data["results"]:
                images.append(result["urls"]["regular"])

    return render_template(
        "image_search.html",
        images=images,
        query=query,
        user=session['user'])
# ------------------------------------------------
# DOWNLOAD IMAGE FROM INTERNET
# ------------------------------------------------
@app.route('/download-image')
def download_image():
    if 'user' not in session:
        return redirect(url_for('login'))

    image_url = request.args.get("url")

    if image_url:
        response = requests.get(image_url)
        return send_file(
            BytesIO(response.content),
            mimetype="image/jpeg",
            as_attachment=True,
            download_name="image.jpg"
        )

    return redirect(url_for('image_search'))

# ------------------------------------------------
# DASHBOARD → Just redirects to HOME (prevents 404)
# ------------------------------------------------
@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect(url_for('login'))
    return redirect(url_for('home'))

# ------------------------------------------------
# HISTORY FROM DATABASE (FIXED)
# ------------------------------------------------
@app.route('/history')
def history():
    if 'user' not in session:
        return redirect(url_for('login'))

    current_user = User.query.filter_by(username=session['user']).first()

    # Only show THIS user's history
    history_data = ImageCaption.query.filter_by(user_id=current_user.id)\
                                     .order_by(ImageCaption.id.desc()).all()

    return render_template("history.html", history=history_data, user=session['user'])


@app.route('/clear-history')
def clear_history():
    if 'user' not in session:
        return redirect(url_for('login'))

    current_user = User.query.filter_by(username=session['user']).first()

    # Only get THIS user's items
    all_items = ImageCaption.query.filter_by(user_id=current_user.id).all()

    for item in all_items:
        file_path = os.path.join("static", item.image_path)
        if os.path.exists(file_path):
            os.remove(file_path)

    # Delete only THIS user's records
    ImageCaption.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()

    return redirect(url_for('history'))


@app.route('/delete-history/<int:id>')
def delete_history(id):
    if 'user' not in session:
        return redirect(url_for('login'))

    current_user = User.query.filter_by(username=session['user']).first()

    # Make sure the item belongs to the logged-in user
    item = ImageCaption.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    image_path = item.image_path

    # Check if this image is used by any OTHER record
    same_image_count = ImageCaption.query.filter(
        ImageCaption.image_path == image_path,
        ImageCaption.id != id
    ).count()

    # Delete DB row first
    db.session.delete(item)
    db.session.commit()

    # Delete file ONLY IF no other record is using it
    if same_image_count == 0:
        file_path = os.path.join("static", image_path)
        if os.path.exists(file_path):
            os.remove(file_path)

    return redirect(url_for('history'))


# ------------------------------------------------
# LOGOUT
# ------------------------------------------------
@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login'))

# ------------------------------------------------
# ADMIN PANEL
# ------------------------------------------------
@app.route('/admin')
def admin():
    if 'user' not in session:
        return redirect(url_for('login'))

    allowed_admins = ["shrey", "jainam"]

    if session['user'].lower() not in allowed_admins:
        return "Access Denied"

    users = User.query.all()
    captions = ImageCaption.query.order_by(ImageCaption.id.desc()).all()

    return render_template("admin.html", users=users, captions=captions)


@app.route('/admin/delete-user/<int:user_id>')
def delete_user(user_id):
    if 'user' not in session:
        return redirect(url_for('login'))

    allowed_admins = ["shrey", "jainam"]

    if session['user'].lower() not in allowed_admins:
        return "Access Denied"

    user = User.query.get_or_404(user_id)

    if user.username.lower() == session['user'].lower():
        return "You cannot delete yourself!"

    db.session.delete(user)
    db.session.commit()

    return redirect(url_for('admin'))

# ------------------------------------------------
# ADMIN - VIEW HISTORY FOR SPECIFIC USER
# ------------------------------------------------
@app.route('/admin/user-history/<int:user_id>')
def view_user_history(user_id):
    if 'user' not in session:
        return redirect(url_for('login'))

    allowed_admins = ["shrey", "jainam"]
    if session['user'].lower() not in allowed_admins:
        return "Access Denied"

    target_user = User.query.get_or_404(user_id)
    captions = ImageCaption.query.filter_by(user_id=user_id).order_by(ImageCaption.id.desc()).all()

    return render_template("user_history.html", target_user=target_user, captions=captions)


# ------------------------------------------------
# hash teg
# ------------------------------------------------

@app.route('/hashtags')
def hashtags():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template("hashtags.html")
# ------------------------------------------------
# RUN APP
# ------------------------------------------------
if __name__ == '__main__':
    app.run(debug=True)


