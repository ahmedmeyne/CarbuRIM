import hashlib
import math
import sqlite3
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

DB_PATH = "stations_nouakchott.db"

FUELS = [
    ("ESSENCE",  "Essence",         "بنزين"),
    ("GASOIL",   "Gasoil / Diesel", "ديزل / غازوال"),
    ("KEROSENE", "Kérosène",        "كيروسين"),
    ("FUEL_OIL", "Fuel-oil",        "فيول أويل"),
]

STATUSES = [
    ("DISPONIBLE", "✅ Disponible", "✅ متوفر"),
    ("RUPTURE",    "❌ Rupture",    "❌ نفاد"),
    ("INCERTAIN",  "❓ Incertain",  "❓ غير محدد"),
]

OVERPASS_URL    = "https://overpass-api.de/api/interpreter"
NOUAKCHOTT_BBOX = (18.00, -16.10, 18.20, -15.85)

CATEGORIES = {
    "INFO":    ("ℹ️ Information", "ℹ️ معلومة"),
    "PROMO":   ("🎁 Promotion",   "🎁 عرض"),
    "ALERTE":  ("⚠️ Alerte",      "⚠️ تنبيه"),
    "SERVICE": ("🔧 Service",     "🔧 خدمة"),
}

DAYS_FR = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
DAYS_AR = ["الاثنين","الثلاثاء","الأربعاء","الخميس","الجمعة","السبت","الأحد"]

# ─── TRADUCTIONS ──────────────────────────────────────────────────────────────

T = {
    "nav_map":          ("🗺️ Carte publique",        "🗺️ الخريطة العامة"),
    "nav_ann":          ("📢 Annonces",              "📢 الإعلانات"),
    "nav_station":      ("🔐 Espace station",        "🔐 فضاء المحطة"),
    "nav_admin":        ("⚙️ Administration",        "⚙️ الإدارة"),
    "filter_title":     ("Filtres",                  "تصفية"),
    "filter_fuel":      ("Carburant",                "الوقود"),
    "filter_status":    ("Statut",                   "الحالة"),
    "filter_all":       ("(tous)",                   "(الكل)"),
    "refresh_osm":      ("🔄 Actualiser OSM",        "🔄 تحديث OSM"),
    "refresh_ok":       ("stations importées.",      "محطة تم استيرادها."),
    "map_title":        ("⛽ CarbuRIM — Nouakchott",  "⛽ CarbuRIM — نواكشوط"),
    "no_station":       ("Aucune station. Cliquez sur '🔄 Actualiser OSM'.",
                         "لا توجد محطات. انقر على '🔄 تحديث OSM'."),
    "nearest_title":    ("📍 Station la plus proche","📍 أقرب محطة"),
    "fuel_wanted":      ("Carburant souhaité",       "الوقود المطلوب"),
    "my_lat":           ("Latitude",                 "خط العرض"),
    "my_lon":           ("Longitude",                "خط الطول"),
    "search_btn":       ("🔍 Chercher",              "🔍 بحث"),
    "no_nearest":       ("Aucune station avec",      "لا توجد محطة بـ"),
    "no_nearest2":      ("disponible.",              "متاح."),
    "details_btn":      ("📋 Détails",               "📋 التفاصيل"),
    "today":            ("Aujourd'hui",              "اليوم"),
    "map_subtitle":     ("Carte interactive",        "الخريطة التفاعلية"),
    "map_caption":      ("Cliquez sur ⛽ pour les détails.", "انقر على ⛽ للتفاصيل."),
    "table_title":      ("Disponibilités",           "التوافر"),
    "col_station":      ("Station",                  "المحطة"),
    "col_fuel":         ("Carburant",                "الوقود"),
    "col_status":       ("Statut",                   "الحالة"),
    "col_updated":      ("Mis à jour",               "آخر تحديث"),
    "col_by":           ("Par",                      "بواسطة"),
    "no_data":          ("Aucune donnée.",            "لا توجد بيانات."),
    "ann_title":        ("📢 Annonces",               "📢 الإعلانات"),
    "ann_empty":        ("Aucune annonce.",           "لا توجد إعلانات."),
    "ann_filter":       ("Filtrer par station",      "تصفية حسب المحطة"),
    "ann_all":          ("Toutes les stations",      "جميع المحطات"),
    "station_login_title": ("🔐 Connexion station",  "🔐 تسجيل الدخول"),
    "station_login_info":  ("Identifiants fournis par l'administrateur.",
                            "البيانات مقدمة من المدير."),
    "login_user":       ("Identifiant",              "اسم المستخدم"),
    "login_pass":       ("Mot de passe",             "كلمة المرور"),
    "login_btn":        ("Se connecter",             "تسجيل الدخول"),
    "login_error":      ("Identifiant ou mot de passe incorrect.",
                         "اسم المستخدم أو كلمة المرور غير صحيحة."),
    "logout_btn":       ("🚪 Déconnexion",           "🚪 خروج"),
    "tab_fuels":        ("⛽ Carburants",            "⛽ الوقود"),
    "tab_ann":          ("📢 Annonces",              "📢 الإعلانات"),
    "tab_hours":        ("🕐 Horaires",              "🕐 الأوقات"),
    "fuel_update":      ("Disponibilités",           "التوافر"),
    "fuel_select_info": ("Statut actuel par carburant :", "الحالة الحالية لكل وقود:"),
    "save_btn":         ("💾 Enregistrer",           "💾 حفظ"),
    "save_ok":          ("✅ Mis à jour !",           "✅ تم التحديث!"),
    "current_state":    ("État actuel",              "الحالة الحالية"),
    "no_status":        ("Aucun statut renseigné.",  "لم يتم إدخال أي حالة."),
    "pub_ann":          ("Publier une annonce",      "نشر إعلان"),
    "ann_cat":          ("Catégorie",                "الفئة"),
    "ann_ttl":          ("Titre",                    "العنوان"),
    "ann_body":         ("Contenu",                  "المحتوى"),
    "pub_btn":          ("📢 Publier",               "📢 نشر"),
    "pub_ok":           ("Publiée !",                "تم النشر!"),
    "pub_warn":         ("Titre et contenu obligatoires.", "العنوان والمحتوى مطلوبان."),
    "my_ann":           ("Mes annonces",             "إعلاناتي"),
    "no_ann":           ("Aucune annonce.",          "لا توجد إعلانات."),
    "published_on":     ("Publiée le",               "نشر في"),
    "deactivate":       ("⏸",                        "⏸"),
    "reactivate":       ("▶️",                       "▶️"),
    "delete":           ("🗑️",                       "🗑️"),
    "hours_title":      ("Horaires d'ouverture",     "أوقات العمل"),
    "hours_caption":    ("Visibles par les usagers.", "مرئية للمستخدمين."),
    "hours_note":       ("Note",                     "ملاحظة"),
    "hours_saved":      ("✅ Enregistré !",           "✅ تم الحفظ!"),
    "hours_current":    ("Horaires enregistrés :",   "الأوقات المسجلة:"),
    "admin_title":      ("⚙️ Administration",        "⚙️ الإدارة"),
    "admin_info":       ("Accès administrateur.",    "وصول المدير."),
    "admin_pass":       ("Mot de passe",             "كلمة المرور"),
    "admin_access":     ("Accéder",                  "دخول"),
    "admin_error":      ("Mot de passe incorrect.",  "كلمة المرور غير صحيحة."),
    "admin_logout":     ("🚪 Déconnexion",           "🚪 خروج"),
    "tab_create":       ("👤 Créer compte",          "👤 إنشاء حساب"),
    "tab_accounts":     ("📋 Comptes",               "📋 الحسابات"),
    "tab_osm":          ("🔄 OSM",                   "🔄 OSM"),
    "create_acc_title": ("Créer un compte station",  "إنشاء حساب محطة"),
    "no_station_warn":  ("Aucune station. Actualisez depuis OSM.",
                         "لا توجد محطات. حدّث من OSM أولاً."),
    "station_lbl":      ("Station",                  "المحطة"),
    "username_lbl":     ("Identifiant",              "اسم المستخدم"),
    "pass_lbl":         ("Mot de passe",             "كلمة المرور"),
    "confirm_lbl":      ("Confirmer",                "تأكيد"),
    "create_btn":       ("Créer",                    "إنشاء"),
    "err_empty_user":   ("Identifiant vide.",        "اسم المستخدم فارغ."),
    "err_pass_diff":    ("Mots de passe différents.","كلمتا المرور غير متطابقتين."),
    "err_pass_short":   ("Minimum 6 caractères.",   "6 أحرف على الأقل."),
    "acc_created":      ("✅ Compte créé pour",      "✅ تم إنشاء حساب لـ"),
    "err_duplicate":    ("Identifiant ou station déjà utilisé.",
                         "مستخدم مسبقاً."),
    "existing_acc":     ("Comptes existants",        "الحسابات الموجودة"),
    "no_accounts":      ("Aucun compte.",            "لا توجد حسابات."),
    "col_id":           ("ID",                       "المعرف"),
    "col_username":     ("Identifiant",              "المستخدم"),
    "col_station_name": ("Station",                  "المحطة"),
    "col_created":      ("Créé le",                  "تاريخ الإنشاء"),
    "osm_title":        ("Actualiser depuis OSM",    "تحديث من OSM"),
    "osm_btn":          ("🔄 Lancer l'import",       "🔄 بدء الاستيراد"),
    "osm_ok":           ("stations importées.",      "محطة تم استيرادها."),
    "osm_loading":      ("Récupération…",            "جارٍ الاسترداد…"),
    "geo_btn":          ("📍 Utiliser ma position GPS", "📍 استخدام موقعي GPS"),
    "geo_wait":         ("Localisation…",            "جارٍ التحديد…"),
    "geo_error":        ("Position non disponible.", "الموقع غير متاح."),
    "geo_denied":       ("Permission refusée.",      "تم رفض الإذن."),
    "geo_or":           ("— ou entrez manuellement —", "— أو أدخل يدوياً —"),
    "map_locate":       ("📍 Ma position",           "📍 موقعي"),
    "here":             ("Vous êtes ici",            "أنت هنا"),
}

def t(key, lang):
    val = T.get(key, (key, key))
    return val[1] if lang == "AR" else val[0]


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def hash_password(p):
    return hashlib.sha256(p.encode()).hexdigest()

def status_label(code, lang="FR"):
    d = {s: (f, a) for s, f, a in STATUSES}
    pair = d.get(code, (code, code))
    return pair[1] if lang == "AR" else pair[0]

def fuel_label(code, lang="FR"):
    d = {s: (f, a) for s, f, a in FUELS}
    pair = d.get(code, (code, code))
    return pair[1] if lang == "AR" else pair[0]

def status_emoji(code):
    return {"DISPONIBLE": "✅", "RUPTURE": "❌", "INCERTAIN": "❓"}.get(code, "•")

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dl  = math.radians(lat2 - lat1)
    dlo = math.radians(lon2 - lon1)
    a   = math.sin(dl/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlo/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def cat_label(cat_key, lang="FR"):
    pair = CATEGORIES.get(cat_key, (cat_key, cat_key))
    return pair[1] if lang == "AR" else pair[0]

def day_label(idx, lang="FR"):
    return DAYS_AR[idx] if lang == "AR" else DAYS_FR[idx].capitalize()

def fuel_list(lang):
    return [(c, ar if lang=="AR" else fr) for c, fr, ar in FUELS]

def status_list(lang):
    return [(c, ar if lang=="AR" else fr) for c, fr, ar in STATUSES]


# ─── OSM ──────────────────────────────────────────────────────────────────────

def overpass_fetch_stations(bbox=NOUAKCHOTT_BBOX):
    s, w, n, e = bbox
    query = f"""[out:json][timeout:25];
    (node["amenity"="fuel"]({s},{w},{n},{e});
     way["amenity"="fuel"]({s},{w},{n},{e});
     relation["amenity"="fuel"]({s},{w},{n},{e}););
    out center tags;"""
    r = requests.post(OVERPASS_URL, data=query.encode(), timeout=60)
    r.raise_for_status()
    rows = []
    for el in r.json().get("elements", []):
        tags = el.get("tags", {}) or {}
        ot   = el.get("type")
        oid  = f"{ot}/{el.get('id')}"
        lat, lon = (el.get("lat"), el.get("lon")) if ot == "node" else \
                   (el.get("center",{}).get("lat"), el.get("center",{}).get("lon"))
        if lat is None: continue
        rows.append({"osm_id": oid, "name": tags.get("name") or "Station-service",
                     "operator": tags.get("operator"), "lat": float(lat), "lon": float(lon),
                     "address": tags.get("addr:full") or tags.get("addr:street")})
    return pd.DataFrame(rows).drop_duplicates(subset=["osm_id"])


# ─── DATABASE ─────────────────────────────────────────────────────────────────

def db():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS stations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, osm_id TEXT UNIQUE,
            name TEXT, operator TEXT, lat REAL, lon REAL, address TEXT)""")
        con.execute("""CREATE TABLE IF NOT EXISTS availability (
            station_id INTEGER, fuel TEXT, status TEXT, updated_at TEXT, updated_by TEXT,
            PRIMARY KEY (station_id, fuel), FOREIGN KEY (station_id) REFERENCES stations(id))""")
        con.execute("""CREATE TABLE IF NOT EXISTS station_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, station_id INTEGER UNIQUE,
            username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, created_at TEXT,
            FOREIGN KEY (station_id) REFERENCES stations(id))""")
        con.execute("""CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT, station_id INTEGER,
            title TEXT NOT NULL, body TEXT NOT NULL, category TEXT DEFAULT 'INFO',
            published_at TEXT, active INTEGER DEFAULT 1,
            FOREIGN KEY (station_id) REFERENCES stations(id))""")
        con.execute("""CREATE TABLE IF NOT EXISTS opening_hours (
            station_id INTEGER PRIMARY KEY, lundi TEXT, mardi TEXT, mercredi TEXT,
            jeudi TEXT, vendredi TEXT, samedi TEXT, dimanche TEXT,
            note TEXT, updated_at TEXT, FOREIGN KEY (station_id) REFERENCES stations(id))""")

def upsert_stations(df):
    with db() as con:
        for _, r in df.iterrows():
            con.execute("""INSERT INTO stations (osm_id,name,operator,lat,lon,address)
                VALUES (?,?,?,?,?,?) ON CONFLICT(osm_id) DO UPDATE SET
                name=excluded.name,operator=excluded.operator,
                lat=excluded.lat,lon=excluded.lon,address=excluded.address""",
                (r["osm_id"],r["name"],r.get("operator"),r["lat"],r["lon"],r.get("address")))

def get_stations():
    with db() as con:
        return pd.read_sql_query("SELECT * FROM stations ORDER BY name", con)

def get_availability():
    with db() as con:
        return pd.read_sql_query("SELECT * FROM availability", con)

def set_status(station_id, fuel, status, updated_by="station"):
    now = datetime.now(timezone.utc).isoformat()
    with db() as con:
        con.execute("""INSERT INTO availability (station_id,fuel,status,updated_at,updated_by)
            VALUES (?,?,?,?,?) ON CONFLICT(station_id,fuel) DO UPDATE SET
            status=excluded.status,updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
            (station_id, fuel, status, now, updated_by))

def create_account(station_id, username, password):
    try:
        with db() as con:
            con.execute("""INSERT INTO station_accounts
                (station_id,username,password_hash,created_at) VALUES (?,?,?,?)""",
                (station_id, username.strip(), hash_password(password),
                 datetime.now(timezone.utc).isoformat()))
        return True
    except sqlite3.IntegrityError:
        return False

def authenticate(username, password):
    with db() as con:
        row = con.execute("""SELECT station_id FROM station_accounts
            WHERE username=? AND password_hash=?""",
            (username.strip(), hash_password(password))).fetchone()
    return row[0] if row else None

def list_accounts():
    with db() as con:
        return pd.read_sql_query("""SELECT sa.id, sa.username, s.name AS station_name, sa.created_at
            FROM station_accounts sa JOIN stations s ON s.id=sa.station_id""", con)

def add_announcement(station_id, title, body, category="INFO"):
    with db() as con:
        con.execute("""INSERT INTO announcements
            (station_id,title,body,category,published_at,active) VALUES (?,?,?,?,?,1)""",
            (station_id, title, body, category, datetime.now(timezone.utc).isoformat()))

def get_announcements(station_id=None, active_only=True):
    with db() as con:
        q = """SELECT a.id,a.station_id,s.name AS station_name,
               a.title,a.body,a.category,a.published_at,a.active
               FROM announcements a JOIN stations s ON s.id=a.station_id"""
        params, filters = [], []
        if station_id:
            filters.append("a.station_id=?"); params.append(station_id)
        if active_only:
            filters.append("a.active=1")
        if filters: q += " WHERE " + " AND ".join(filters)
        q += " ORDER BY a.published_at DESC"
        return pd.read_sql_query(q, con, params=params)

def toggle_announcement(ann_id, active):
    with db() as con:
        con.execute("UPDATE announcements SET active=? WHERE id=?", (active, ann_id))

def delete_announcement(ann_id):
    with db() as con:
        con.execute("DELETE FROM announcements WHERE id=?", (ann_id,))

def save_opening_hours(station_id, hours, note):
    with db() as con:
        con.execute("""INSERT INTO opening_hours
            (station_id,lundi,mardi,mercredi,jeudi,vendredi,samedi,dimanche,note,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(station_id) DO UPDATE SET
            lundi=excluded.lundi,mardi=excluded.mardi,mercredi=excluded.mercredi,
            jeudi=excluded.jeudi,vendredi=excluded.vendredi,samedi=excluded.samedi,
            dimanche=excluded.dimanche,note=excluded.note,updated_at=excluded.updated_at""",
            (station_id,hours.get("lundi",""),hours.get("mardi",""),hours.get("mercredi",""),
             hours.get("jeudi",""),hours.get("vendredi",""),hours.get("samedi",""),
             hours.get("dimanche",""),note,datetime.now(timezone.utc).isoformat()))

def get_opening_hours(station_id):
    with db() as con:
        row = con.execute("SELECT * FROM opening_hours WHERE station_id=?", (station_id,)).fetchone()
        if row:
            cols = [d[0] for d in con.execute("PRAGMA table_info(opening_hours)").fetchall()]
            return dict(zip(cols, row))
    return {}

def logout():
    for k in ["station_logged_in","station_id","station_name"]:
        st.session_state.pop(k, None)


# ─── CSS RESPONSIVE ───────────────────────────────────────────────────────────

def inject_responsive_css(lang):
    rtl_content = "direction:rtl; text-align:right;" if lang == "AR" else ""
    font = "Cairo, sans-serif" if lang == "AR" else "Inter, sans-serif"
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700&family=Inter:wght@400;500;600&display=swap');

    /* Police globale */
    html, body, [class*="css"] {{ font-family: {font}; }}

    /* RTL uniquement sur le contenu — jamais sur sidebar ni layout */
    .main .block-container {{ {rtl_content} }}
    .main .block-container h1,
    .main .block-container h2,
    .main .block-container h3,
    .main .block-container p,
    .main .block-container label,
    .main .block-container span,
    .main .block-container div {{ font-family: {font}; }}

    /* Sidebar : toujours à gauche, direction LTR forcée */
    [data-testid="stSidebar"] {{
        direction: ltr !important;
        min-width: 220px !important;
        max-width: 260px !important;
        font-family: {font};
    }}
    [data-testid="stSidebar"] > div:first-child {{
        padding: 1rem 0.8rem;
    }}

    /* Topbar Streamlit — masquée sur mobile pour gagner de l'espace */
    @media (max-width: 640px) {{
        header[data-testid="stHeader"] {{
            display: none !important;
        }}
        /* Compenser le padding-top que la topbar prenait */
        .block-container {{
            padding-top: 0.5rem !important;
        }}
    }}

    /* Contenu principal — desktop */
    .block-container {{
        padding-left: 1rem !important;
        padding-right: 1rem !important;
        padding-top: 2rem !important;
        max-width: 100% !important;
    }}

    /* ── Titres adaptatifs ──────────────────────────────── */
    h1 {{
        font-size: clamp(1.1rem, 5vw, 1.8rem) !important;
        line-height: 1.3 !important;
        margin-bottom: 0.6rem !important;
        word-break: break-word;
    }}
    h2 {{ font-size: clamp(1rem, 3.5vw, 1.4rem) !important; }}
    h3 {{ font-size: clamp(0.9rem, 3vw, 1.2rem) !important; }}

    /* ── Boutons — tactile friendly ─────────────────────── */
    .stButton > button {{
        min-height: 48px;
        font-size: 15px;
        border-radius: 8px;
        width: 100%;
        touch-action: manipulation;
        font-family: {font};
    }}

    /* ── Inputs — évite zoom iOS (font >= 16px) ─────────── */
    input, textarea, select {{
        font-size: 16px !important;
        min-height: 44px;
        font-family: {font} !important;
    }}

    /* ── Selectbox ──────────────────────────────────────── */
    [data-testid="stSelectbox"] > div {{
        min-height: 44px;
    }}

    /* ── Radio buttons — espacement tactile ─────────────── */
    [data-testid="stRadio"] label {{
        padding: 6px 10px;
        cursor: pointer;
    }}

    /* ── Tabs — scrollables sur petits écrans ───────────── */
    [data-testid="stTabs"] [role="tablist"] {{
        overflow-x: auto;
        flex-wrap: nowrap;
        -webkit-overflow-scrolling: touch;
        scrollbar-width: none;
    }}
    [data-testid="stTabs"] [role="tablist"]::-webkit-scrollbar {{ display: none; }}
    [data-testid="stTabs"] [role="tab"] {{
        white-space: nowrap;
        min-width: fit-content;
        padding: 8px 14px;
        font-size: 14px;
    }}

    /* ── Dataframe — scroll horizontal ──────────────────── */
    [data-testid="stDataFrame"] {{
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
    }}

    /* ── Cards / containers ─────────────────────────────── */
    [data-testid="stVerticalBlockBorderWrapper"] {{
        border-radius: 10px !important;
        padding: 0.8rem !important;
    }}

    /* ── MOBILE < 640px ─────────────────────────────────── */
    @media (max-width: 640px) {{
        iframe {{
            width: 100% !important;
        }}
        [data-testid="stSidebar"] {{
            min-width: 0 !important;
        }}
        /* Colonnes en stack vertical */
        [data-testid="column"] {{
            width: 100% !important;
            flex: 1 1 100% !important;
            min-width: 100% !important;
        }}
        h1 {{ font-size: 1.15rem !important; padding-top: 0.2rem; }}
        .stButton > button {{
            font-size: 16px;
            padding: 14px;
            min-height: 52px;
        }}
        /* Caption et petits textes lisibles */
        [data-testid="stCaptionContainer"] p {{
            font-size: 13px !important;
        }}
        /* Alert messages */
        [data-testid="stAlert"] {{
            font-size: 14px;
            padding: 10px 12px;
        }}
        /* Subheader */
        h3 {{ font-size: 1rem !important; }}
    }}

    /* ── TABLETTE 641–1024px ─────────────────────────────── */
    @media (min-width: 641px) and (max-width: 1024px) {{
        [data-testid="column"] {{
            min-width: 45% !important;
        }}
        h1 {{ font-size: 1.4rem !important; }}
    }}

    /* ── Expander ────────────────────────────────────────── */
    [data-testid="stExpander"] summary {{
        font-size: 15px;
        padding: 10px 0;
    }}

    /* ── Alerts ──────────────────────────────────────────── */
    [data-testid="stAlert"] {{
        border-radius: 8px;
        font-size: 14px;
    }}

    /* ── Divider ─────────────────────────────────────────── */
    hr {{ margin: 0.5rem 0 !important; }}

    </style>
    """, unsafe_allow_html=True)


# ─── CARTE LEAFLET ────────────────────────────────────────────────────────────

def build_leaflet_map(stations_df, avail_grp, lang, fuel_code="ESSENCE", highlight_sid=None, height=540):
    # ── Données stations pour la recherche JS ──────────────────────────────────
    stations_json = []
    for _, s in stations_df.iterrows():
        sid = int(s["id"])
        a   = avail_grp.get(sid, {})
        stt = a.get(fuel_code, {}).get("status", "")
        stations_json.append({
            "id": sid, "name": s["name"],
            "lat": s["lat"], "lon": s["lon"],
            "status": stt,
            "addr": s.get("address") or "",
            "op":   s.get("operator") or ""
        })

    import json as _json
    stations_js_data = _json.dumps(stations_json, ensure_ascii=False)

    # ── Marqueurs ──────────────────────────────────────────────────────────────
    markers_js = ""
    for _, s in stations_df.iterrows():
        sid  = int(s["id"])
        a    = avail_grp.get(sid, {})
        name = s["name"].replace("'", "\\'").replace("`","'")
        addr = (s.get("address") or "Nouakchott").replace("'", "\\'").replace("`","'")
        op   = (s.get("operator") or "").replace("'", "\\'").replace("`","'")

        statuses = [v.get("status") for v in a.values() if v.get("status")]
        if   "DISPONIBLE" in statuses: color = "#16a34a"
        elif "RUPTURE"    in statuses: color = "#dc2626"
        elif "INCERTAIN"  in statuses: color = "#d97706"
        else:                          color = "#6b7280"

        rows = ""
        for f_code, f_fr, f_ar in FUELS:
            fname = f_ar if lang == "AR" else f_fr
            stt   = a.get(f_code, {}).get("status")
            upd   = a.get(f_code, {}).get("updated_at", "")
            upd_s = f"<small style='color:#888'> ({upd[:10]})</small>" if upd else ""
            em    = status_emoji(stt) if stt else "•"
            lab   = status_label(stt, lang) if stt else ("<i>غير مسجل</i>" if lang=="AR" else "<i>Non renseigné</i>")
            rows += f"<tr><td style='padding:3px 8px 3px 0;white-space:nowrap'>{fname}</td><td>{em} {lab}{upd_s}</td></tr>"

        ann = get_announcements(station_id=sid, active_only=True)
        ann_html = ""
        if not ann.empty:
            ann_html = "<hr style='margin:5px 0'/><b>" + ("📢 الإعلانات:" if lang=="AR" else "📢 Annonces :") + "</b><br/>"
            for _, row in ann.iterrows():
                cl = cat_label(row["category"], lang)
                tt = row["title"].replace("'","\\'").replace("`","'")
                bb = row["body"][:80].replace("'","\\'").replace("`","'")
                ann_html += f"<b>{cl} {tt}</b><br/><small>{bb}…</small><br/>"

        h = get_opening_hours(sid)
        hours_html = ""
        if h:
            today_idx = datetime.now().weekday()
            day_name  = DAYS_AR[today_idx] if lang=="AR" else DAYS_FR[today_idx].capitalize()
            val = h.get(DAYS_FR[today_idx], "")
            if val:
                lbl = "اليوم" if lang=="AR" else "Aujourd'hui"
                hours_html = f"<hr style='margin:5px 0'/><b>🕐 {lbl} ({day_name}) :</b> {val}"

        border  = "border:3px solid gold;" if sid == highlight_sid else ""
        rtl     = "direction:rtl;text-align:right;" if lang=="AR" else ""
        th_al   = "right" if lang=="AR" else "left"

        popup = (
            f"<div style='min-width:230px;max-width:290px;font-family:sans-serif;{border}{rtl}'>"
            f"<h4 style='margin:0 0 3px;font-size:14px'>⛽ {name}</h4>"
            f"<small style='color:#555'>{op}{' — ' if op else ''}{addr}</small>"
            f"<hr style='margin:5px 0'/>"
            f"<table style='font-size:12px;width:100%'>"
            f"<tr><th style='text-align:{th_al}'>{'الوقود' if lang=='AR' else 'Carburant'}</th>"
            f"<th style='text-align:{th_al}'>{'الحالة' if lang=='AR' else 'Statut'}</th></tr>"
            f"{rows}</table>{hours_html}{ann_html}</div>"
        ).replace("`","'").replace("\n"," ")

        svg = (
            f"<svg xmlns='http://www.w3.org/2000/svg' width='34' height='44' viewBox='0 0 34 44'>"
            f"<ellipse cx='17' cy='42' rx='7' ry='2' fill='rgba(0,0,0,0.2)'/>"
            f"<path d='M17 0 C8 0 2 7 2 16 C2 28 17 42 17 42 C17 42 32 28 32 16 C32 7 26 0 17 0Z' "
            f"fill='{color}' stroke='white' stroke-width='2'/>"
            f"<text x='17' y='21' text-anchor='middle' font-size='16' fill='white'>⛽</text>"
            f"</svg>"
        )

        markers_js += f"""
        L.marker([{s['lat']}, {s['lon']}], {{
            icon: L.divIcon({{html:`{svg}`,iconSize:[34,44],iconAnchor:[17,44],popupAnchor:[0,-44],className:''}})
        }}).bindPopup(`{popup}`,{{maxWidth:300}})
          .bindTooltip('⛽ {name}',{{direction:'top'}})
          .addTo(map);
        """

    # ── Labels i18n pour le JS ─────────────────────────────────────────────────
    leg_avail   = "متوفر"    if lang=="AR" else "Disponible"
    leg_rupt    = "نفاد"     if lang=="AR" else "Rupture"
    leg_inc     = "غير محدد" if lang=="AR" else "Incertain"
    leg_unk     = "غير مسجل" if lang=="AR" else "Non renseigné"
    leg_ttl     = "المفتاح"  if lang=="AR" else "Légende"
    leg_rtl     = "direction:rtl;text-align:right;" if lang=="AR" else ""
    leg_side    = "right"    if lang=="AR" else "left"
    btn_locate  = "📍 موقعي" if lang=="AR" else "📍 Ma position"
    btn_search  = "🔍 ابحث عن أقرب محطة" if lang=="AR" else "🔍 Trouver la station la plus proche"
    lbl_wait    = "جارٍ التحديد…" if lang=="AR" else "Localisation…"
    lbl_err     = "الموقع غير متاح" if lang=="AR" else "Position non disponible"
    lbl_deny    = "تم رفض الإذن — فعّل الموقع في إعدادات المتصفح" if lang=="AR" else "Permission refusée — activez la géolocalisation dans votre navigateur"
    lbl_none    = "لا توجد محطة بهذا الوقود قريبة منك" if lang=="AR" else "Aucune station avec ce carburant disponible à proximité"
    lbl_found   = "أقرب محطة" if lang=="AR" else "Station la plus proche"
    lbl_km      = "كم"        if lang=="AR" else "km"
    lbl_here    = "أنت هنا"   if lang=="AR" else "Vous êtes ici"

    html = f"""<!DOCTYPE html><html><head>
    <meta charset='utf-8'/>
    <meta name='viewport' content='width=device-width,initial-scale=1'/>
    <link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'/>
    <script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script>
    <style>
      *{{box-sizing:border-box;margin:0;padding:0}}
      html,body,#map{{height:100%;width:100%}}

      /* ── Panneau de contrôle bas de carte ── */
      #ctrl{{
        position:absolute;bottom:0;left:0;right:0;z-index:1000;
        background:rgba(255,255,255,0.97);
        padding:10px 12px;
        display:flex;gap:8px;align-items:center;
        box-shadow:0 -2px 8px rgba(0,0,0,.15);
        flex-wrap:wrap;
      }}
      #btnLocate{{
        background:#1d6f42;color:white;border:none;border-radius:7px;
        padding:10px 14px;font-size:14px;font-weight:600;cursor:pointer;
        touch-action:manipulation;white-space:nowrap;min-height:44px;
        flex-shrink:0;
      }}
      #btnLocate:disabled{{background:#7fb89a;cursor:not-allowed}}
      #btnLocate:active{{background:#155a34}}
      #btnSearch{{
        background:#2563eb;color:white;border:none;border-radius:7px;
        padding:10px 14px;font-size:14px;font-weight:600;cursor:pointer;
        touch-action:manipulation;white-space:nowrap;min-height:44px;
        flex:1;
      }}
      #btnSearch:disabled{{background:#93b4f0;cursor:not-allowed}}
      #btnSearch:active{{background:#1d4ed8}}
      #geoStatus{{
        font-size:12px;color:#555;width:100%;margin-top:4px;
        {'text-align:right' if lang=='AR' else ''};
      }}

      /* ── Légende ── */
      .legend{{
        position:absolute;top:10px;{leg_side}:10px;z-index:1000;
        background:white;padding:7px 10px;border-radius:8px;
        border:1px solid #ccc;font-size:11px;
        box-shadow:2px 2px 6px rgba(0,0,0,.2);
        line-height:1.9;{leg_rtl}
      }}

      /* ── Popup mobile ── */
      .leaflet-popup-content{{max-width:85vw!important}}
      .leaflet-popup-content-wrapper{{border-radius:10px!important}}

      @media(max-width:480px){{
        #ctrl{{padding:8px 10px;gap:6px}}
        #btnLocate,#btnSearch{{font-size:13px;padding:9px 10px}}
        .legend{{font-size:10px;padding:5px 7px}}
      }}
    </style>
    </head>
    <body>
    <div id='map'></div>

    <div id='ctrl'>
      <button id='btnLocate' onclick='locateMe()'>{btn_locate}</button>
      <button id='btnSearch' onclick='findNearest()' disabled>{btn_search}</button>
      <div id='geoStatus'></div>
    </div>

    <div class='legend'>
      <b>{leg_ttl}</b><br>
      <span style='color:#16a34a;font-size:14px'>●</span> {leg_avail}<br>
      <span style='color:#dc2626;font-size:14px'>●</span> {leg_rupt}<br>
      <span style='color:#d97706;font-size:14px'>●</span> {leg_inc}<br>
      <span style='color:#6b7280;font-size:14px'>●</span> {leg_unk}
    </div>

    <script>
    var map = L.map('map',{{zoomControl:true,tap:true}}).setView([18.086,-15.965],13);
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{
      attribution:'© OpenStreetMap',maxZoom:19
    }}).addTo(map);

    {markers_js}

    // Données stations pour la recherche
    var stationsData = {stations_js_data};

    var userMarker  = null;
    var nearMarker  = null;
    var userLat     = null;
    var userLon     = null;

    function haversine(lat1,lon1,lat2,lon2){{
      var R=6371, dLat=(lat2-lat1)*Math.PI/180, dLon=(lon2-lon1)*Math.PI/180;
      var a=Math.sin(dLat/2)*Math.sin(dLat/2)+
            Math.cos(lat1*Math.PI/180)*Math.cos(lat2*Math.PI/180)*
            Math.sin(dLon/2)*Math.sin(dLon/2);
      return R*2*Math.atan2(Math.sqrt(a),Math.sqrt(1-a));
    }}

    function locateMe(){{
      var btn = document.getElementById('btnLocate');
      var st  = document.getElementById('geoStatus');
      if(!navigator.geolocation){{ st.style.color='#dc2626'; st.innerText='{lbl_err}'; return; }}
      btn.disabled=true; btn.textContent='⏳ {lbl_wait}';
      st.style.color='#888'; st.innerText='';

      navigator.geolocation.getCurrentPosition(
        function(pos){{
          userLat = pos.coords.latitude;
          userLon = pos.coords.longitude;

          // Marqueur position utilisateur
          if(userMarker) map.removeLayer(userMarker);
          userMarker = L.circleMarker([userLat,userLon],{{
            radius:10,fillColor:'#3b82f6',color:'white',
            weight:3,opacity:1,fillOpacity:1
          }}).addTo(map).bindPopup('{lbl_here}');
          map.setView([userLat,userLon],14);

          st.style.color='#16a34a';
          st.innerText='✅ '+userLat.toFixed(4)+', '+userLon.toFixed(4);
          btn.disabled=false; btn.textContent='{btn_locate}';
          document.getElementById('btnSearch').disabled=false;
        }},
        function(err){{
          st.style.color='#dc2626';
          st.innerText=err.code===1?'{lbl_deny}':'{lbl_err}';
          btn.disabled=false; btn.textContent='{btn_locate}';
        }},
        {{enableHighAccuracy:true,timeout:15000,maximumAge:0}}
      );
    }}

    function findNearest(){{
      if(userLat===null) return;
      var st = document.getElementById('geoStatus');

      // Trouver la station la plus proche avec statut DISPONIBLE
      var best=null, bestDist=Infinity;
      stationsData.forEach(function(s){{
        if(s.status!=='DISPONIBLE') return;
        var d=haversine(userLat,userLon,s.lat,s.lon);
        if(d<bestDist){{ bestDist=d; best=s; }}
      }});

      if(nearMarker){{ map.removeLayer(nearMarker); nearMarker=null; }}

      if(!best){{
        st.style.color='#d97706';
        st.innerText='⚠️ {lbl_none}';
        return;
      }}

      // Marqueur doré sur la station trouvée
      var goldSvg = `<svg xmlns='http://www.w3.org/2000/svg' width='40' height='52' viewBox='0 0 40 52'>
        <ellipse cx='20' cy='50' rx='8' ry='2.5' fill='rgba(0,0,0,0.25)'/>
        <path d='M20 0 C9 0 2 8 2 18 C2 32 20 50 20 50 C20 50 38 32 38 18 C38 8 31 0 20 0Z'
              fill='#f59e0b' stroke='white' stroke-width='2.5'/>
        <text x='20' y='24' text-anchor='middle' font-size='18' fill='white'>⛽</text>
      </svg>`;
      nearMarker = L.marker([best.lat,best.lon],{{
        icon:L.divIcon({{html:goldSvg,iconSize:[40,52],iconAnchor:[20,52],popupAnchor:[0,-52],className:''}})
      }}).addTo(map);

      var dist = bestDist<1 ? (bestDist*1000).toFixed(0)+' m' : bestDist.toFixed(2)+' {lbl_km}';
      nearMarker.bindPopup(
        '<div style="font-family:sans-serif;min-width:200px">' +
        '<b style="color:#f59e0b;font-size:15px">⭐ {lbl_found}</b><br/>' +
        '<b>⛽ '+best.name+'</b><br/>' +
        '<span style="color:#16a34a;font-weight:600">📍 '+dist+'</span>' +
        (best.addr?'<br/><small>'+best.addr+'</small>':'') +
        '</div>',
        {{maxWidth:260}}
      ).openPopup();

      // Zoom pour voir utilisateur + station
      var bounds = L.latLngBounds([[userLat,userLon],[best.lat,best.lon]]);
      map.fitBounds(bounds,{{padding:[40,40]}});

      st.style.color='#16a34a';
      st.innerText='⭐ '+best.name+' — '+dist;
    }}
    </script>
    </body></html>"""

    components.html(html, height=height, scrolling=False)


# ══════════════════════════════════════════════════════════════════════════════
# APP
# ══════════════════════════════════════════════════════════════════════════════

st.set_page_config(page_title="CarbuRIM", page_icon="⛽", layout="wide")
init_db()
stations = get_stations()

# ─── SIDEBAR ──────────────────────────────────────────────────────────────────
with st.sidebar:
    lang_choice = st.radio(
        "🌐", ["🇫🇷 Français", "🇲🇷 عربي"],
        horizontal=True, key="lang_radio", label_visibility="collapsed"
    )
    lang = "AR" if "عربي" in lang_choice else "FR"
    st.session_state["lang"] = lang

    st.divider()
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/4/43/Flag_of_Mauritania.svg/320px-Flag_of_Mauritania.svg.png", width=70)
    st.title("⛽ CarbuRIM")

    nav_options = [t("nav_map",lang), t("nav_ann",lang), t("nav_station",lang), t("nav_admin",lang)]
    page = st.radio("Nav", nav_options, label_visibility="collapsed")
    st.divider()

    if page == t("nav_map", lang):
        st.caption(t("filter_title", lang))
        fuel_all_lbl   = t("filter_all", lang)
        status_all_lbl = t("filter_all", lang)
        fuel_filter    = st.selectbox(t("filter_fuel",lang),   [fuel_all_lbl]   + [fn for _,fn in fuel_list(lang)])
        status_filter  = st.selectbox(t("filter_status",lang), [status_all_lbl] + [sn for _,sn in status_list(lang)])
        st.divider()
        if st.button(t("refresh_osm", lang), use_container_width=True):
            with st.spinner(t("osm_loading", lang)):
                df_osm = overpass_fetch_stations()
                upsert_stations(df_osm)
            st.success(f"{len(df_osm)} {t('refresh_ok', lang)}")
            st.rerun()

# ─── CSS GLOBAL ───────────────────────────────────────────────────────────────
inject_responsive_css(lang)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE : CARTE
# ══════════════════════════════════════════════════════════════════════════════
if page == t("nav_map", lang):
    st.title(t("map_title", lang))

    if stations.empty:
        st.warning(t("no_station", lang))
        st.stop()

    avail = get_availability()
    avail_grp = {}
    if not avail.empty:
        for sid, grp in avail.groupby("station_id"):
            avail_grp[int(sid)] = grp.set_index("fuel")[["status","updated_at"]].to_dict("index")

    # ── Recherche station la plus proche ──────────────────────────────────────
    with st.container(border=True):
        st.subheader(t("nearest_title", lang))
        fuel_names       = [fn for _,fn in fuel_list(lang)]
        fuel_search_name = st.selectbox(t("fuel_wanted", lang), fuel_names, key="fs")
        fuel_code_sel    = next((code for code,fn in fuel_list(lang) if fn == fuel_search_name), "ESSENCE")

        # Message d'info : la recherche se fait via la carte
        if lang == "AR":
            st.info("📍 انقر على زر **'ابحث عن أقرب محطة'** في الخريطة أدناه للعثور على أقرب محطة تلقائياً.")
        else:
            st.info("📍 Cliquez sur **'Trouver la station la plus proche'** dans la carte ci-dessous — votre position GPS sera détectée automatiquement.")

    highlight_sid = st.session_state.get("highlight_sid")
    nearest_info  = st.session_state.get("nearest_info")

    st.divider()

    # ── Carte interactive (géoloc + recherche intégrées) ──────────────────────
    st.subheader(t("map_subtitle", lang))
    st.caption(t("map_caption", lang))
    build_leaflet_map(stations, avail_grp, lang,
                      fuel_code=fuel_code_sel,
                      highlight_sid=highlight_sid, height=520)

    st.divider()
    st.subheader(t("table_title", lang))
    df      = stations.merge(avail, left_on="id", right_on="station_id", how="left")
    df_view = df.copy()
    if fuel_filter != fuel_all_lbl:
        fc_f = next((c for c,fn in fuel_list(lang) if fn == fuel_filter), None)
        if fc_f: df_view = df_view[df_view["fuel"] == fc_f]
    if status_filter != status_all_lbl:
        sc_f = next((c for c,sn in status_list(lang) if sn == status_filter), None)
        if sc_f: df_view = df_view[df_view["status"] == sc_f]
    if not df_view.empty:
        show = df_view[["name","fuel","status","updated_at"]].copy()
        show["fuel"]   = show["fuel"].apply(lambda c: fuel_label(c, lang))
        show["status"] = show["status"].apply(lambda c: status_label(c, lang))
        show.columns   = [t("col_station",lang), t("col_fuel",lang), t("col_status",lang), t("col_updated",lang)]
        st.dataframe(show.dropna(subset=[t("col_status",lang)]), use_container_width=True, height=300)
    else:
        st.info(t("no_data", lang))


# ══════════════════════════════════════════════════════════════════════════════
# PAGE : ANNONCES
# ══════════════════════════════════════════════════════════════════════════════
elif page == t("nav_ann", lang):
    st.title(t("ann_title", lang))
    ann = get_announcements(active_only=True)
    if ann.empty:
        st.info(t("ann_empty", lang))
    else:
        all_label = t("ann_all", lang)
        opts = [all_label] + ann["station_name"].unique().tolist()
        f_st = st.selectbox(t("ann_filter", lang), opts)
        if f_st != all_label:
            ann = ann[ann["station_name"] == f_st]
        for _, row in ann.iterrows():
            dt = row["published_at"][:10] if row["published_at"] else ""
            with st.container(border=True):
                st.markdown(f"**{cat_label(row['category'],lang)} — {row['title']}**")
                st.caption(f"🏪 {row['station_name']}  •  📅 {dt}")
                st.markdown(row["body"])


# ══════════════════════════════════════════════════════════════════════════════
# PAGE : ESPACE STATION
# ══════════════════════════════════════════════════════════════════════════════
elif page == t("nav_station", lang):
    if not st.session_state.get("station_logged_in"):
        st.title(t("station_login_title", lang))
        st.markdown(t("station_login_info", lang))
        with st.form("login_form"):
            username  = st.text_input(t("login_user", lang))
            password  = st.text_input(t("login_pass", lang), type="password")
            submitted = st.form_submit_button(t("login_btn", lang), use_container_width=True)
        if submitted:
            sid = authenticate(username, password)
            if sid:
                with db() as con:
                    row = con.execute("SELECT name FROM stations WHERE id=?", (sid,)).fetchone()
                st.session_state.update({"station_logged_in": True, "station_id": sid,
                                         "station_name": row[0] if row else f"Station #{sid}"})
                st.rerun()
            else:
                st.error(t("login_error", lang))
    else:
        sid   = st.session_state["station_id"]
        sname = st.session_state["station_name"]
        col_title, col_out = st.columns([3,1])
        col_title.title(f"🏪 {sname}")
        with col_out:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button(t("logout_btn", lang), use_container_width=True):
                logout(); st.rerun()

        tab1, tab2, tab3 = st.tabs([t("tab_fuels",lang), t("tab_ann",lang), t("tab_hours",lang)])

        with tab1:
            st.subheader(t("fuel_update", lang))
            av   = get_availability()
            av_s = av[av["station_id"]==sid] if not av.empty else pd.DataFrame()
            av_d = av_s.set_index("fuel")["status"].to_dict() if not av_s.empty else {}
            sopts = [code for code,_,_ in STATUSES]
            st.caption(t("fuel_select_info", lang))
            with st.form("fuel_form"):
                ns = {}
                for fc, f_fr, f_ar in FUELS:
                    fname = f_ar if lang=="AR" else f_fr
                    cur   = av_d.get(fc, "INCERTAIN")
                    idx   = sopts.index(cur) if cur in sopts else 2
                    st.markdown(f"**{fname}**")
                    ns[fc] = st.radio(fname, sopts, index=idx,
                                      format_func=lambda c: status_label(c, lang),
                                      horizontal=True, label_visibility="collapsed", key=f"f_{fc}")
                    st.divider()
                if st.form_submit_button(t("save_btn", lang), use_container_width=True):
                    for fc, fs in ns.items():
                        set_status(sid, fc, fs, updated_by=sname)
                    st.success(t("save_ok", lang)); st.rerun()

            st.subheader(t("current_state", lang))
            fresh  = get_availability()
            av_now = fresh[fresh["station_id"]==sid] if not fresh.empty else pd.DataFrame()
            if not av_now.empty:
                for _, row in av_now.iterrows():
                    with st.container(border=True):
                        ca, cb = st.columns(2)
                        ca.markdown(f"**{fuel_label(row['fuel'], lang)}**")
                        cb.markdown(status_label(row["status"], lang))
                        upd = row.get("updated_at","")
                        if upd: st.caption(f"🕐 {upd[:16].replace('T',' ')} UTC")
            else:
                st.info(t("no_status", lang))

        with tab2:
            st.subheader(t("pub_ann", lang))
            with st.form("ann_form"):
                ann_cat   = st.selectbox(t("ann_cat",lang), list(CATEGORIES.keys()),
                                         format_func=lambda k: cat_label(k, lang))
                ann_title = st.text_input(t("ann_ttl", lang))
                ann_body  = st.text_area(t("ann_body", lang), height=120)
                if st.form_submit_button(t("pub_btn", lang), use_container_width=True):
                    if ann_title.strip() and ann_body.strip():
                        add_announcement(sid, ann_title.strip(), ann_body.strip(), ann_cat)
                        st.success(t("pub_ok", lang)); st.rerun()
                    else:
                        st.warning(t("pub_warn", lang))

            st.subheader(t("my_ann", lang))
            my_ann = get_announcements(station_id=sid, active_only=False)
            if my_ann.empty:
                st.info(t("no_ann", lang))
            else:
                for _, row in my_ann.iterrows():
                    active = bool(row["active"])
                    dt = row["published_at"][:16].replace("T"," ") if row["published_at"] else ""
                    with st.container(border=True):
                        st.markdown(f"**{cat_label(row['category'],lang)} — {row['title']}** {'🟢' if active else '🔴'}")
                        st.caption(f"{t('published_on',lang)} {dt}")
                        st.markdown(row["body"])
                        cb1, cb2 = st.columns(2)
                        lbl = t("deactivate",lang) if active else t("reactivate",lang)
                        if cb1.button(lbl, key=f"tog_{row['id']}", use_container_width=True):
                            toggle_announcement(row["id"], 0 if active else 1); st.rerun()
                        if cb2.button(t("delete",lang), key=f"del_{row['id']}", use_container_width=True):
                            delete_announcement(row["id"]); st.rerun()

        with tab3:
            st.subheader(t("hours_title", lang))
            st.caption(t("hours_caption", lang))
            existing = get_opening_hours(sid)
            with st.form("hours_form"):
                hi   = {}
                cols = st.columns(2)
                for i, day_fr in enumerate(DAYS_FR):
                    with cols[i % 2]:
                        dlbl    = DAYS_AR[i] if lang=="AR" else day_fr.capitalize()
                        hi[day_fr] = st.text_input(dlbl, value=existing.get(day_fr,""),
                                                    placeholder="07:00–22:00")
                note = st.text_area(t("hours_note",lang), value=existing.get("note",""))
                if st.form_submit_button(t("save_btn",lang), use_container_width=True):
                    save_opening_hours(sid, hi, note)
                    st.success(t("hours_saved",lang)); st.rerun()
            h = get_opening_hours(sid)
            if h:
                st.divider()
                st.markdown(f"**{t('hours_current',lang)}**")
                for i, day_fr in enumerate(DAYS_FR):
                    if h.get(day_fr):
                        dlbl = DAYS_AR[i] if lang=="AR" else day_fr.capitalize()
                        st.write(f"**{dlbl}** : {h[day_fr]}")
                if h.get("note"): st.info(h["note"])


# ══════════════════════════════════════════════════════════════════════════════
# PAGE : ADMINISTRATION
# ══════════════════════════════════════════════════════════════════════════════
elif page == t("nav_admin", lang):
    st.title(t("admin_title", lang))
    try:    ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin1234")
    except: ADMIN_PASSWORD = "admin1234"

    if not st.session_state.get("admin_logged_in"):
        st.markdown(t("admin_info", lang))
        with st.form("admin_login"):
            pwd = st.text_input(t("admin_pass", lang), type="password")
            if st.form_submit_button(t("admin_access", lang), use_container_width=True):
                if pwd == ADMIN_PASSWORD:
                    st.session_state["admin_logged_in"] = True; st.rerun()
                else:
                    st.error(t("admin_error", lang))
    else:
        if st.button(t("admin_logout", lang), use_container_width=True):
            st.session_state.pop("admin_logged_in", None); st.rerun()

        tab_a, tab_b, tab_c = st.tabs([t("tab_create",lang), t("tab_accounts",lang), t("tab_osm",lang)])

        with tab_a:
            st.subheader(t("create_acc_title", lang))
            if stations.empty:
                st.warning(t("no_station_warn", lang))
            else:
                opts = stations.apply(lambda r: f"{r['name']} (id={r['id']})", axis=1).tolist()
                with st.form("create_account_form"):
                    s_choice  = st.selectbox(t("station_lbl",lang), opts)
                    new_user  = st.text_input(t("username_lbl",lang))
                    new_pass  = st.text_input(t("pass_lbl",lang), type="password")
                    new_pass2 = st.text_input(t("confirm_lbl",lang), type="password")
                    if st.form_submit_button(t("create_btn",lang), use_container_width=True):
                        s_id = int(s_choice.split("id=")[1].replace(")",""))
                        if not new_user.strip():    st.error(t("err_empty_user",lang))
                        elif new_pass != new_pass2: st.error(t("err_pass_diff",lang))
                        elif len(new_pass) < 6:     st.error(t("err_pass_short",lang))
                        elif create_account(s_id, new_user, new_pass):
                            st.success(f"{t('acc_created',lang)} {s_choice.split(' (id=')[0]}.")
                        else:
                            st.error(t("err_duplicate",lang))

        with tab_b:
            st.subheader(t("existing_acc", lang))
            accounts = list_accounts()
            if accounts.empty:
                st.info(t("no_accounts", lang))
            else:
                accounts.columns = [t("col_id",lang), t("col_username",lang),
                                     t("col_station_name",lang), t("col_created",lang)]
                st.dataframe(accounts, use_container_width=True)

        with tab_c:
            st.subheader(t("osm_title", lang))
            if st.button(t("osm_btn", lang), use_container_width=True):
                with st.spinner(t("osm_loading", lang)):
                    df_osm = overpass_fetch_stations()
                    upsert_stations(df_osm)
                st.success(f"✅ {len(df_osm)} {t('osm_ok',lang)}"); st.rerun()
