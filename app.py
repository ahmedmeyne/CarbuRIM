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
    ("ESSENCE",  "Essence"),
    ("GASOIL",   "Gasoil / Diesel"),
    ("KEROSENE", "Kérosène"),
    ("FUEL_OIL", "Fuel-oil"),
]

STATUSES = [
    ("DISPONIBLE", "✅ Disponible"),
    ("RUPTURE",    "❌ Rupture"),
    ("INCERTAIN",  "❓ Incertain"),
]

OVERPASS_URL    = "https://overpass-api.de/api/interpreter"
NOUAKCHOTT_BBOX = (18.00, -16.10, 18.20, -15.85)

CATEGORIES = {
    "INFO":    "ℹ️ Information",
    "PROMO":   "🎁 Promotion",
    "ALERTE":  "⚠️ Alerte",
    "SERVICE": "🔧 Service",
}

DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def hash_password(p):
    return hashlib.sha256(p.encode()).hexdigest()

def status_label(code):
    return dict(STATUSES).get(code, code)

def fuel_label(code):
    return dict(FUELS).get(code, code)

def status_emoji(code):
    return {"DISPONIBLE": "✅", "RUPTURE": "❌", "INCERTAIN": "❓"}.get(code, "•")

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dl = math.radians(lat2 - lat1)
    dlo = math.radians(lon2 - lon1)
    a = math.sin(dl/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlo/2)**2
    return R * 2 * math.asin(math.sqrt(a))


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
                name=excluded.name,operator=excluded.operator,lat=excluded.lat,
                lon=excluded.lon,address=excluded.address""",
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


# ─── CARTE LEAFLET (HTML natif) ───────────────────────────────────────────────

def build_leaflet_map(stations_df, avail_grp, highlight_sid=None, height=560):
    """Génère une carte Leaflet via CDN (pas de pip install) avec marqueurs ⛽ et popups."""

    markers_js = ""
    for _, s in stations_df.iterrows():
        sid   = int(s["id"])
        a     = avail_grp.get(sid, {})
        name  = s["name"].replace("'", "\\'")
        addr  = (s.get("address") or "Nouakchott").replace("'", "\\'")
        op    = (s.get("operator") or "").replace("'", "\\'")

        statuses = [v.get("status") for v in a.values() if v.get("status")]
        if   "DISPONIBLE" in statuses: color = "#16a34a"
        elif "RUPTURE"    in statuses: color = "#dc2626"
        elif "INCERTAIN"  in statuses: color = "#d97706"
        else:                          color = "#6b7280"

        # Tableau carburants
        rows = ""
        for f_code, f_name in FUELS:
            stt  = a.get(f_code, {}).get("status")
            upd  = a.get(f_code, {}).get("updated_at", "")
            upd_s = f" <small style='color:#888'>({upd[:10]})</small>" if upd else ""
            em   = status_emoji(stt) if stt else "•"
            lab  = status_label(stt) if stt else "<i>Non renseigné</i>"
            rows += f"<tr><td style='padding:2px 8px 2px 0'>{f_name}</td><td>{em} {lab}{upd_s}</td></tr>"

        # Annonces
        ann     = get_announcements(station_id=sid, active_only=True)
        ann_html = ""
        if not ann.empty:
            ann_html = "<hr style='margin:6px 0'/><b>📢 Annonces :</b><br/>"
            for _, row in ann.iterrows():
                t = row["title"].replace("'", "\\'")
                b = row["body"][:100].replace("'", "\\'")
                ann_html += f"<b>{CATEGORIES.get(row['category'],'')} {t}</b><br/><small>{b}…</small><br/>"

        # Horaires
        h = get_opening_hours(sid)
        hours_html = ""
        if h:
            today = DAYS[datetime.now().weekday()]
            val   = h.get(today, "")
            if val:
                hours_html = f"<hr style='margin:6px 0'/><b>🕐 Aujourd'hui ({today.capitalize()}) :</b> {val}"

        border = "border:3px solid gold;" if sid == highlight_sid else ""

        popup = (
            f"<div style='min-width:260px;font-family:sans-serif;{border}'>"
            f"<h4 style='margin:0 0 4px'>⛽ {name}</h4>"
            f"<small style='color:#555'>{op} — {addr}</small>"
            f"<hr style='margin:6px 0'/>"
            f"<table style='font-size:13px;width:100%'><tr><th style='text-align:left'>Carburant</th>"
            f"<th style='text-align:left'>Statut</th></tr>{rows}</table>"
            f"{hours_html}{ann_html}</div>"
        ).replace("`", "'").replace("\n", " ")

        # Icône SVG ⛽ colorée
        svg = (
            f"<svg xmlns='http://www.w3.org/2000/svg' width='34' height='44' viewBox='0 0 34 44'>"
            f"<ellipse cx='17' cy='42' rx='7' ry='2' fill='rgba(0,0,0,0.2)'/>"
            f"<path d='M17 0 C8 0 2 7 2 16 C2 28 17 42 17 42 C17 42 32 28 32 16 C32 7 26 0 17 0Z' fill='{color}' stroke='white' stroke-width='2'/>"
            f"<text x='17' y='21' text-anchor='middle' font-size='16' fill='white'>⛽</text>"
            f"</svg>"
        )

        markers_js += f"""
        L.marker([{s['lat']}, {s['lon']}], {{
            icon: L.divIcon({{
                html: `{svg}`,
                iconSize: [34, 44],
                iconAnchor: [17, 44],
                popupAnchor: [0, -44],
                className: ''
            }})
        }}).bindPopup(`{popup}`, {{maxWidth: 320}})
          .bindTooltip('⛽ {name}', {{direction:'top'}})
          .addTo(map);
        """

    html = f"""<!DOCTYPE html><html><head>
    <meta charset='utf-8'/>
    <link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'/>
    <script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script>
    <style>
      html,body,#map {{margin:0;padding:0;height:100%;width:100%;}}
      .legend {{position:absolute;bottom:24px;left:12px;z-index:1000;background:white;
                padding:8px 12px;border-radius:8px;border:1px solid #ccc;
                font-size:12px;box-shadow:2px 2px 6px rgba(0,0,0,.2);line-height:1.6}}
    </style>
    </head><body>
    <div id='map'></div>
    <div class='legend'>
      <b>Légende</b><br>
      <span style='color:#16a34a'>●</span> Disponible<br>
      <span style='color:#dc2626'>●</span> Rupture<br>
      <span style='color:#d97706'>●</span> Incertain<br>
      <span style='color:#6b7280'>●</span> Non renseigné
    </div>
    <script>
      var map = L.map('map', {{zoomControl:true}}).setView([18.086, -15.965], 13);
      L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
        attribution:'© OpenStreetMap contributors', maxZoom:19
      }}).addTo(map);
      {markers_js}
    </script>
    </body></html>"""

    components.html(html, height=height, scrolling=False)


# ══════════════════════════════════════════════════════════════════════════════
# APP
# ══════════════════════════════════════════════════════════════════════════════

st.set_page_config(page_title="CarbuRIM", page_icon="⛽", layout="wide")
init_db()
stations = get_stations()

with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/4/43/Flag_of_Mauritania.svg/320px-Flag_of_Mauritania.svg.png", width=80)
    st.title("⛽ CarbuRIM")
    page = st.radio("Navigation",
        ["🗺️ Carte publique", "📢 Annonces", "🔐 Espace station", "⚙️ Administration"],
        label_visibility="collapsed")
    st.divider()
    if page == "🗺️ Carte publique":
        st.header("Filtres")
        fuel_filter   = st.selectbox("Carburant", ["(tous)"] + [f for f,_ in FUELS])
        status_filter = st.selectbox("Statut",    ["(tous)"] + [s for s,_ in STATUSES])
        st.divider()
        if st.button("🔄 Actualiser depuis OSM"):
            with st.spinner("Récupération…"):
                df_osm = overpass_fetch_stations()
                upsert_stations(df_osm)
            st.success(f"{len(df_osm)} stations importées.")
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
if page == "🗺️ Carte publique":
    st.title("⛽ CarbuRIM — Carburants & disponibilité à Nouakchott")

    if stations.empty:
        st.warning("Aucune station en base. Cliquez sur '🔄 Actualiser depuis OSM'.")
        st.stop()

    avail = get_availability()
    avail_grp = {}
    if not avail.empty:
        for sid, grp in avail.groupby("station_id"):
            avail_grp[int(sid)] = grp.set_index("fuel")[["status","updated_at"]].to_dict("index")

    # ── Recherche station la plus proche ──────────────────────────────────────
    with st.container(border=True):
        st.subheader("📍 Station la plus proche avec carburant disponible")
        col_a, col_b = st.columns([1, 2])
        with col_a:
            fuel_search = st.selectbox("Carburant souhaité", [f for _,f in FUELS], key="fs")
        with col_b:
            c1, c2, c3 = st.columns([2,2,1])
            user_lat = c1.number_input("Latitude", value=18.0860, format="%.5f")
            user_lon = c2.number_input("Longitude", value=-15.9650, format="%.5f")
            c3.markdown("<br>", unsafe_allow_html=True)
            search = c3.button("🔍 Chercher")

    highlight_sid = st.session_state.get("highlight_sid")
    nearest_info  = st.session_state.get("nearest_info")

    if search:
        fuel_code  = {v: k for k,v in dict(FUELS).items()}[fuel_search]
        candidates = []
        for _, s in stations.iterrows():
            sid = int(s["id"])
            stt = avail_grp.get(sid, {}).get(fuel_code, {}).get("status")
            if stt == "DISPONIBLE":
                d = haversine(user_lat, user_lon, s["lat"], s["lon"])
                candidates.append((d, sid, s["name"], s["lat"], s["lon"],
                                   s.get("address",""), s.get("operator","")))
        if candidates:
            candidates.sort()
            best = candidates[0]
            st.session_state["highlight_sid"] = best[1]
            st.session_state["nearest_info"]  = best
            highlight_sid = best[1]
            nearest_info  = best
        else:
            st.warning(f"Aucune station avec **{fuel_search}** disponible.")
            st.session_state.pop("highlight_sid", None)
            st.session_state.pop("nearest_info",  None)
            highlight_sid = None
            nearest_info  = None

    if nearest_info:
        dist, sid, name, nlat, nlon, addr, op = nearest_info
        st.success(f"⛽ **{name}** — à **{dist:.2f} km** (marqueur doré sur la carte)")
        with st.expander("📋 Détails de la station"):
            if addr: st.write(f"📍 {addr}")
            if op:   st.write(f"🏢 {op}")
            h = get_opening_hours(sid)
            if h:
                today = DAYS[datetime.now().weekday()]
                if h.get(today): st.write(f"🕐 Aujourd'hui : {h[today]}")
            a = avail_grp.get(sid, {})
            for f_code, f_name in FUELS:
                stt = a.get(f_code, {}).get("status")
                if stt: st.write(f"{status_emoji(stt)} {f_name} : {status_label(stt)}")

    st.divider()

    # ── Carte + tableau ────────────────────────────────────────────────────────
    col1, col2 = st.columns([2, 1], gap="large")

    with col1:
        st.subheader("Carte interactive")
        st.caption("Cliquez sur un marqueur ⛽ pour voir les détails.")
        build_leaflet_map(stations, avail_grp, highlight_sid=highlight_sid)

    with col2:
        st.subheader("Tableau des disponibilités")
        df = stations.merge(avail, left_on="id", right_on="station_id", how="left")
        df_view = df.copy()
        if fuel_filter   != "(tous)": df_view = df_view[df_view["fuel"]   == fuel_filter]
        if status_filter != "(tous)": df_view = df_view[df_view["status"] == status_filter]
        if not df_view.empty:
            show = df_view[["name","fuel","status","updated_at","updated_by"]].copy()
            show["fuel"]   = show["fuel"].map(dict(FUELS))
            show["status"] = show["status"].map(dict(STATUSES))
            show.columns   = ["Station","Carburant","Statut","Mis à jour","Par"]
            st.dataframe(show.dropna(subset=["Statut"]), use_container_width=True, height=520)
        else:
            st.info("Aucune donnée selon les filtres.")


# ══════════════════════════════════════════════════════════════════════════════
elif page == "📢 Annonces":
    st.title("📢 CarbuRIM — Annonces des stations")
    ann = get_announcements(active_only=True)
    if ann.empty:
        st.info("Aucune annonce publiée pour le moment.")
    else:
        opts = ["Toutes les stations"] + ann["station_name"].unique().tolist()
        f_st = st.selectbox("Filtrer par station", opts)
        if f_st != "Toutes les stations":
            ann = ann[ann["station_name"] == f_st]
        for _, row in ann.iterrows():
            dt = row["published_at"][:10] if row["published_at"] else ""
            with st.container(border=True):
                st.markdown(f"**{CATEGORIES.get(row['category'],row['category'])} — {row['title']}**")
                st.markdown(f"🏪 *{row['station_name']}* &nbsp;|&nbsp; 📅 {dt}")
                st.markdown(row["body"])


# ══════════════════════════════════════════════════════════════════════════════
elif page == "🔐 Espace station":
    if not st.session_state.get("station_logged_in"):
        st.title("🔐 Espace station — Connexion")
        st.markdown("Connectez-vous avec les identifiants fournis par votre administrateur.")
        with st.form("login_form"):
            username  = st.text_input("Identifiant")
            password  = st.text_input("Mot de passe", type="password")
            submitted = st.form_submit_button("Se connecter")
        if submitted:
            sid = authenticate(username, password)
            if sid:
                with db() as con:
                    row = con.execute("SELECT name FROM stations WHERE id=?", (sid,)).fetchone()
                st.session_state.update({"station_logged_in": True,
                                         "station_id": sid,
                                         "station_name": row[0] if row else f"Station #{sid}"})
                st.rerun()
            else:
                st.error("Identifiant ou mot de passe incorrect.")
    else:
        sid   = st.session_state["station_id"]
        sname = st.session_state["station_name"]
        st.title(f"🏪 {sname}")
        _, col_out = st.columns([4,1])
        with col_out:
            if st.button("🚪 Déconnexion"): logout(); st.rerun()

        tab1, tab2, tab3 = st.tabs(["⛽ Carburants","📢 Annonces","🕐 Horaires"])

        with tab1:
            st.subheader("Disponibilités carburant")
            av = get_availability()
            av_s = av[av["station_id"]==sid] if not av.empty else pd.DataFrame()
            av_d = av_s.set_index("fuel")["status"].to_dict() if not av_s.empty else {}
            sopts = [s for s,_ in STATUSES]
            with st.form("fuel_form"):
                ns = {}
                for fc, fn in FUELS:
                    cur = av_d.get(fc, "INCERTAIN")
                    idx = sopts.index(cur) if cur in sopts else 2
                    cf, cs = st.columns([2,3])
                    cf.markdown(f"**{fn}**")
                    with cs:
                        ns[fc] = st.radio(fn, sopts, index=idx, format_func=status_label,
                                          horizontal=True, label_visibility="collapsed", key=f"f_{fc}")
                if st.form_submit_button("💾 Enregistrer"):
                    for fc, fs in ns.items():
                        set_status(sid, fc, fs, updated_by=sname)
                    st.success("✅ Mis à jour !"); st.rerun()
            st.divider()
            st.markdown("**État actuel :**")
            fresh = get_availability()
            av_now = fresh[fresh["station_id"]==sid] if not fresh.empty else pd.DataFrame()
            if not av_now.empty:
                for _, row in av_now.iterrows():
                    ca,cb,cc = st.columns([2,2,3])
                    ca.write(fuel_label(row["fuel"]))
                    cb.write(status_label(row["status"]))
                    upd = row.get("updated_at","")
                    cc.caption(f"{upd[:16].replace('T',' ')} UTC" if upd else "")
            else:
                st.info("Aucun statut renseigné.")

        with tab2:
            st.subheader("Publier une annonce")
            with st.form("ann_form"):
                ann_cat   = st.selectbox("Catégorie", list(CATEGORIES.keys()), format_func=lambda k: CATEGORIES[k])
                ann_title = st.text_input("Titre")
                ann_body  = st.text_area("Contenu", height=100)
                if st.form_submit_button("📢 Publier"):
                    if ann_title.strip() and ann_body.strip():
                        add_announcement(sid, ann_title.strip(), ann_body.strip(), ann_cat)
                        st.success("Publiée !"); st.rerun()
                    else: st.warning("Titre et contenu obligatoires.")
            st.divider()
            st.subheader("Mes annonces")
            my_ann = get_announcements(station_id=sid, active_only=False)
            if my_ann.empty: st.info("Aucune annonce.")
            else:
                for _, row in my_ann.iterrows():
                    active = bool(row["active"])
                    dt = row["published_at"][:16].replace("T"," ") if row["published_at"] else ""
                    with st.container(border=True):
                        c1,c2,c3 = st.columns([4,1,1])
                        with c1:
                            st.markdown(f"**{CATEGORIES.get(row['category'],'')} — {row['title']}** {'🟢' if active else '🔴'}")
                            st.caption(f"Publiée le {dt}")
                            st.markdown(row["body"])
                        with c2:
                            if st.button("⏸" if active else "▶️", key=f"tog_{row['id']}"):
                                toggle_announcement(row["id"], 0 if active else 1); st.rerun()
                        with c3:
                            if st.button("🗑️", key=f"del_{row['id']}"):
                                delete_announcement(row["id"]); st.rerun()

        with tab3:
            st.subheader("Horaires d'ouverture")
            existing = get_opening_hours(sid)
            with st.form("hours_form"):
                hi = {}
                cols = st.columns(2)
                for i, day in enumerate(DAYS):
                    with cols[i%2]:
                        hi[day] = st.text_input(day.capitalize(),
                                                 value=existing.get(day,""),
                                                 placeholder="ex: 07:00–22:00")
                note = st.text_area("Note", value=existing.get("note",""))
                if st.form_submit_button("💾 Enregistrer"):
                    save_opening_hours(sid, hi, note)
                    st.success("✅ Enregistré !"); st.rerun()
            h = get_opening_hours(sid)
            if h:
                st.divider()
                for day in DAYS:
                    if h.get(day): st.write(f"**{day.capitalize()}** : {h[day]}")
                if h.get("note"): st.info(h["note"])


# ══════════════════════════════════════════════════════════════════════════════
elif page == "⚙️ Administration":
    st.title("⚙️ CarbuRIM — Administration")
    try:    ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin1234")
    except: ADMIN_PASSWORD = "admin1234"

    if not st.session_state.get("admin_logged_in"):
        st.markdown("Accès réservé à l'administrateur.")
        with st.form("admin_login"):
            pwd = st.text_input("Mot de passe", type="password")
            if st.form_submit_button("Accéder"):
                if pwd == ADMIN_PASSWORD:
                    st.session_state["admin_logged_in"] = True; st.rerun()
                else: st.error("Mot de passe incorrect.")
    else:
        if st.button("🚪 Déconnexion admin"):
            st.session_state.pop("admin_logged_in", None); st.rerun()

        tab_a, tab_b, tab_c = st.tabs(["👤 Créer un compte","📋 Comptes existants","🔄 OSM"])

        with tab_a:
            st.subheader("Créer un compte station")
            if stations.empty:
                st.warning("Aucune station. Actualisez d'abord depuis OSM.")
            else:
                opts = stations.apply(lambda r: f"{r['name']} (id={r['id']})", axis=1).tolist()
                with st.form("create_account_form"):
                    s_choice  = st.selectbox("Station", opts)
                    new_user  = st.text_input("Identifiant")
                    new_pass  = st.text_input("Mot de passe", type="password")
                    new_pass2 = st.text_input("Confirmer", type="password")
                    if st.form_submit_button("Créer"):
                        s_id = int(s_choice.split("id=")[1].replace(")",""))
                        if not new_user.strip():    st.error("Identifiant vide.")
                        elif new_pass != new_pass2: st.error("Mots de passe différents.")
                        elif len(new_pass) < 6:     st.error("Minimum 6 caractères.")
                        elif create_account(s_id, new_user, new_pass):
                            st.success(f"✅ Compte créé pour {s_choice.split(' (id=')[0]}.")
                        else: st.error("Identifiant ou station déjà utilisé.")

        with tab_b:
            st.subheader("Comptes existants")
            accounts = list_accounts()
            if accounts.empty: st.info("Aucun compte.")
            else:
                accounts.columns = ["ID","Identifiant","Station","Créé le"]
                st.dataframe(accounts, use_container_width=True)

        with tab_c:
            st.subheader("Actualiser depuis OSM")
            if st.button("🔄 Lancer l'import"):
                with st.spinner("Récupération…"):
                    df_osm = overpass_fetch_stations()
                    upsert_stations(df_osm)
                st.success(f"✅ {len(df_osm)} stations importées."); st.rerun()
