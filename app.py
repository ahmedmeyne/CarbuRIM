import hashlib
import math
import sqlite3
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
import folium
from folium.plugins import LocateControl, MarkerCluster
from streamlit_folium import st_folium

DB_PATH = "stations_nouakchott.db"

FUELS = [
    ("ESSENCE", "Essence"),
    ("GASOIL", "Gasoil / Diesel"),
    ("KEROSENE", "Kérosène"),
    ("FUEL_OIL", "Fuel-oil"),
]

STATUSES = [
    ("DISPONIBLE", "✅ Disponible"),
    ("RUPTURE", "❌ Rupture"),
    ("INCERTAIN", "❓ Incertain"),
]

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOUAKCHOTT_BBOX = (18.00, -16.10, 18.20, -15.85)

MARKER_COLORS = {
    "DISPONIBLE": "green",
    "RUPTURE":    "red",
    "INCERTAIN":  "orange",
    "UNKNOWN":    "gray",
}


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def hash_password(p: str) -> str:
    return hashlib.sha256(p.encode()).hexdigest()

def status_label(code: str) -> str:
    return dict(STATUSES).get(code, code)

def fuel_label(code: str) -> str:
    return dict(FUELS).get(code, code)

def status_emoji(code: str) -> str:
    return {"DISPONIBLE": "✅", "RUPTURE": "❌", "INCERTAIN": "❓"}.get(code, "•")

def haversine(lat1, lon1, lat2, lon2) -> float:
    """Distance en km entre deux points GPS."""
    R = 6371
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = math.sin(d_lat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


# ─── OSM ──────────────────────────────────────────────────────────────────────

def overpass_fetch_stations(bbox=NOUAKCHOTT_BBOX) -> pd.DataFrame:
    south, west, north, east = bbox
    query = f"""
    [out:json][timeout:25];
    (
      node["amenity"="fuel"]({south},{west},{north},{east});
      way["amenity"="fuel"]({south},{west},{north},{east});
      relation["amenity"="fuel"]({south},{west},{north},{east});
    );
    out center tags;
    """
    r = requests.post(OVERPASS_URL, data=query.encode("utf-8"), timeout=60)
    r.raise_for_status()
    js = r.json()
    rows = []
    for el in js.get("elements", []):
        tags = el.get("tags", {}) or {}
        osm_type = el.get("type")
        osm_id = f"{osm_type}/{el.get('id')}"
        if osm_type == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            c = el.get("center") or {}
            lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            continue
        rows.append({
            "osm_id": osm_id,
            "name": tags.get("name") or "Station-service",
            "operator": tags.get("operator"),
            "lat": float(lat),
            "lon": float(lon),
            "address": tags.get("addr:full") or tags.get("addr:street"),
        })
    return pd.DataFrame(rows).drop_duplicates(subset=["osm_id"])


# ─── DATABASE ─────────────────────────────────────────────────────────────────

def db():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS stations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            osm_id TEXT UNIQUE, name TEXT, operator TEXT,
            lat REAL, lon REAL, address TEXT)""")
        con.execute("""CREATE TABLE IF NOT EXISTS availability (
            station_id INTEGER, fuel TEXT, status TEXT,
            updated_at TEXT, updated_by TEXT,
            PRIMARY KEY (station_id, fuel),
            FOREIGN KEY (station_id) REFERENCES stations(id))""")
        con.execute("""CREATE TABLE IF NOT EXISTS station_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id INTEGER UNIQUE, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, created_at TEXT,
            FOREIGN KEY (station_id) REFERENCES stations(id))""")
        con.execute("""CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id INTEGER, title TEXT NOT NULL, body TEXT NOT NULL,
            category TEXT DEFAULT 'INFO', published_at TEXT, active INTEGER DEFAULT 1,
            FOREIGN KEY (station_id) REFERENCES stations(id))""")
        con.execute("""CREATE TABLE IF NOT EXISTS opening_hours (
            station_id INTEGER PRIMARY KEY,
            lundi TEXT, mardi TEXT, mercredi TEXT, jeudi TEXT,
            vendredi TEXT, samedi TEXT, dimanche TEXT,
            note TEXT, updated_at TEXT,
            FOREIGN KEY (station_id) REFERENCES stations(id))""")

def upsert_stations(df: pd.DataFrame):
    with db() as con:
        for _, row in df.iterrows():
            con.execute("""
            INSERT INTO stations (osm_id, name, operator, lat, lon, address)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(osm_id) DO UPDATE SET
                name=excluded.name, operator=excluded.operator,
                lat=excluded.lat, lon=excluded.lon, address=excluded.address
            """, (row["osm_id"], row["name"], row.get("operator"),
                  row["lat"], row["lon"], row.get("address")))

def get_stations() -> pd.DataFrame:
    with db() as con:
        return pd.read_sql_query("SELECT * FROM stations ORDER BY name", con)

def get_availability() -> pd.DataFrame:
    with db() as con:
        return pd.read_sql_query("SELECT * FROM availability", con)

def set_status(station_id, fuel, status, updated_by="station"):
    now = datetime.now(timezone.utc).isoformat()
    with db() as con:
        con.execute("""
        INSERT INTO availability (station_id, fuel, status, updated_at, updated_by)
        VALUES (?,?,?,?,?)
        ON CONFLICT(station_id, fuel) DO UPDATE SET
            status=excluded.status, updated_at=excluded.updated_at,
            updated_by=excluded.updated_by
        """, (station_id, fuel, status, now, updated_by))

def create_account(station_id, username, password) -> bool:
    try:
        with db() as con:
            con.execute("""INSERT INTO station_accounts
                (station_id, username, password_hash, created_at) VALUES (?,?,?,?)""",
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

def list_accounts() -> pd.DataFrame:
    with db() as con:
        return pd.read_sql_query("""
            SELECT sa.id, sa.username, s.name AS station_name, sa.created_at
            FROM station_accounts sa JOIN stations s ON s.id=sa.station_id""", con)

CATEGORIES = {
    "INFO": "ℹ️ Information", "PROMO": "🎁 Promotion",
    "ALERTE": "⚠️ Alerte",   "SERVICE": "🔧 Service",
}

def add_announcement(station_id, title, body, category="INFO"):
    with db() as con:
        con.execute("""INSERT INTO announcements
            (station_id, title, body, category, published_at, active) VALUES (?,?,?,?,?,1)""",
            (station_id, title, body, category, datetime.now(timezone.utc).isoformat()))

def get_announcements(station_id=None, active_only=True) -> pd.DataFrame:
    with db() as con:
        q = """SELECT a.id, a.station_id, s.name AS station_name,
               a.title, a.body, a.category, a.published_at, a.active
               FROM announcements a JOIN stations s ON s.id=a.station_id"""
        params, filters = [], []
        if station_id:
            filters.append("a.station_id=?"); params.append(station_id)
        if active_only:
            filters.append("a.active=1")
        if filters:
            q += " WHERE " + " AND ".join(filters)
        q += " ORDER BY a.published_at DESC"
        return pd.read_sql_query(q, con, params=params)

def toggle_announcement(ann_id, active):
    with db() as con:
        con.execute("UPDATE announcements SET active=? WHERE id=?", (active, ann_id))

def delete_announcement(ann_id):
    with db() as con:
        con.execute("DELETE FROM announcements WHERE id=?", (ann_id,))

DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

def save_opening_hours(station_id, hours, note):
    with db() as con:
        con.execute("""INSERT INTO opening_hours
            (station_id,lundi,mardi,mercredi,jeudi,vendredi,samedi,dimanche,note,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(station_id) DO UPDATE SET
            lundi=excluded.lundi, mardi=excluded.mardi, mercredi=excluded.mercredi,
            jeudi=excluded.jeudi, vendredi=excluded.vendredi, samedi=excluded.samedi,
            dimanche=excluded.dimanche, note=excluded.note, updated_at=excluded.updated_at""",
            (station_id, hours.get("lundi",""), hours.get("mardi",""),
             hours.get("mercredi",""), hours.get("jeudi",""), hours.get("vendredi",""),
             hours.get("samedi",""), hours.get("dimanche",""), note,
             datetime.now(timezone.utc).isoformat()))

def get_opening_hours(station_id) -> dict:
    with db() as con:
        row = con.execute("SELECT * FROM opening_hours WHERE station_id=?", (station_id,)).fetchone()
        if row:
            cols = [d[0] for d in con.execute("PRAGMA table_info(opening_hours)").fetchall()]
            return dict(zip(cols, row))
    return {}

def logout():
    for k in ["station_logged_in", "station_id", "station_name"]:
        st.session_state.pop(k, None)


# ─── CARTE FOLIUM ─────────────────────────────────────────────────────────────

def build_map(stations, avail_grp, highlight_sid=None):
    center_lat = stations["lat"].mean()
    center_lon = stations["lon"].mean()

    m = folium.Map(location=[center_lat, center_lon], zoom_start=13,
                   tiles="OpenStreetMap")

    # Bouton de géolocalisation
    LocateControl(auto_start=False, position="topright",
                  strings={"title": "Ma position"}).add_to(m)

    for _, s in stations.iterrows():
        sid = int(s["id"])
        a = avail_grp.get(sid, {})

        # Couleur selon statut dominant
        statuses = [v.get("status") for v in a.values() if v.get("status")]
        if "DISPONIBLE" in statuses:
            color = "green"
        elif "RUPTURE" in statuses:
            color = "red"
        elif "INCERTAIN" in statuses:
            color = "orange"
        else:
            color = "gray"

        # Icône ⛽ personnalisée
        icon = folium.Icon(
            color="white" if sid == highlight_sid else color,
            icon_color=color if sid == highlight_sid else "white",
            icon="tint",
            prefix="fa",
        )

        # Construction du popup HTML
        rows_html = ""
        for f_code, f_name in FUELS:
            stt = a.get(f_code, {}).get("status")
            upd = a.get(f_code, {}).get("updated_at", "")
            upd_str = f"<small style='color:#888'> — {upd[:10]}</small>" if upd else ""
            emoji = status_emoji(stt) if stt else "•"
            label = status_label(stt) if stt else "<i>Non renseigné</i>"
            rows_html += f"<tr><td>{f_name}</td><td>{emoji} {label}{upd_str}</td></tr>"

        # Annonces
        ann = get_announcements(station_id=sid, active_only=True)
        ann_html = ""
        if not ann.empty:
            ann_html = "<hr style='margin:6px 0'/><b>📢 Annonces :</b><br/>"
            for _, row in ann.iterrows():
                ann_html += f"<b>{CATEGORIES.get(row['category'],row['category'])} {row['title']}</b><br/>"
                ann_html += f"<small>{row['body'][:100]}{'…' if len(row['body'])>100 else ''}</small><br/>"

        # Horaires
        h = get_opening_hours(sid)
        hours_html = ""
        if h:
            today = DAYS[datetime.now().weekday()]
            val = h.get(today, "")
            if val:
                hours_html = f"<hr style='margin:6px 0'/><b>🕐 Aujourd'hui ({today.capitalize()}) :</b> {val}"

        highlight_style = "border:3px solid gold;" if sid == highlight_sid else ""

        popup_html = f"""
        <div style='min-width:260px;font-family:sans-serif;{highlight_style}'>
          <h4 style='margin:0 0 4px 0'>⛽ {s['name']}</h4>
          <small style='color:#555'>{s.get('operator') or ''} — {s.get('address') or 'Nouakchott'}</small>
          <hr style='margin:6px 0'/>
          <table style='width:100%;font-size:13px'>
            <tr><th style='text-align:left'>Carburant</th><th style='text-align:left'>Statut</th></tr>
            {rows_html}
          </table>
          {hours_html}
          {ann_html}
        </div>
        """

        folium.Marker(
            location=[s["lat"], s["lon"]],
            popup=folium.Popup(popup_html, max_width=320),
            tooltip=f"⛽ {s['name']}",
            icon=icon,
        ).add_to(m)

    # Légende
    legend_html = """
    <div style='position:fixed;bottom:30px;left:30px;z-index:1000;
                background:white;padding:10px 14px;border-radius:8px;
                border:1px solid #ccc;font-size:13px;box-shadow:2px 2px 6px rgba(0,0,0,0.2)'>
      <b>Légende</b><br>
      <span style='color:green'>●</span> Disponible<br>
      <span style='color:red'>●</span> Rupture<br>
      <span style='color:orange'>●</span> Incertain<br>
      <span style='color:gray'>●</span> Non renseigné
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    return m


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
        st.header("Filtres carte")
        fuel_filter = st.selectbox("Carburant", ["(tous)"] + [f for f, _ in FUELS])
        status_filter = st.selectbox("Statut", ["(tous)"] + [s for s, _ in STATUSES])
        st.divider()
        if st.button("🔄 Actualiser depuis OSM"):
            with st.spinner("Récupération…"):
                df_osm = overpass_fetch_stations()
                upsert_stations(df_osm)
            st.success(f"{len(df_osm)} stations importées.")
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE : CARTE PUBLIQUE
# ══════════════════════════════════════════════════════════════════════════════
if page == "🗺️ Carte publique":
    st.title("⛽ CarbuRIM — Carburants & disponibilité à Nouakchott")

    if stations.empty:
        st.warning("Aucune station en base. Cliquez sur 'Actualiser depuis OSM'.")
        st.stop()

    avail = get_availability()

    # Construire avail_grp
    avail_grp = {}
    if not avail.empty:
        for sid, grp in avail.groupby("station_id"):
            avail_grp[int(sid)] = grp.set_index("fuel")[["status","updated_at"]].to_dict("index")

    # ── Section : Station la plus proche ──────────────────────────────────────
    st.subheader("📍 Trouver la station la plus proche")

    with st.container(border=True):
        col_a, col_b, col_c = st.columns([2, 2, 1])
        with col_a:
            fuel_search = st.selectbox("Carburant souhaité",
                [f_name for _, f_name in FUELS],
                key="fuel_search")
        with col_b:
            user_lat = st.number_input("Ma latitude", value=18.086, format="%.6f", key="ulat")
            user_lon = st.number_input("Ma longitude", value=-15.965, format="%.6f", key="ulon")
        with col_c:
            st.markdown("<br>", unsafe_allow_html=True)
            search_btn = st.button("🔍 Chercher", use_container_width=True)

    highlight_sid = None
    nearest_info = None

    if search_btn:
        fuel_code = dict((v,k) for k,v in dict(FUELS).items()).get(fuel_search)
        candidates = []
        for _, s in stations.iterrows():
            sid = int(s["id"])
            a = avail_grp.get(sid, {})
            stt = a.get(fuel_code, {}).get("status")
            if stt == "DISPONIBLE":
                dist = haversine(user_lat, user_lon, s["lat"], s["lon"])
                candidates.append((dist, sid, s["name"], s["lat"], s["lon"],
                                   s.get("address",""), s.get("operator","")))

        if candidates:
            candidates.sort(key=lambda x: x[0])
            best = candidates[0]
            highlight_sid = best[1]
            nearest_info = best
            st.session_state["highlight_sid"] = highlight_sid
            st.session_state["nearest_info"] = nearest_info
        else:
            st.warning(f"Aucune station avec {fuel_search} disponible trouvée.")
            st.session_state.pop("highlight_sid", None)
            st.session_state.pop("nearest_info", None)

    # Récupérer depuis session si déjà cherché
    if not highlight_sid and "highlight_sid" in st.session_state:
        highlight_sid = st.session_state["highlight_sid"]
        nearest_info = st.session_state.get("nearest_info")

    # Afficher le résultat
    if nearest_info:
        dist, sid, name, nlat, nlon, addr, operator = nearest_info
        st.success(f"⛽ Station la plus proche avec **{fuel_search}** disponible : **{name}** — à **{dist:.2f} km** de vous")
        with st.expander("📋 Voir les détails de cette station"):
            if addr:
                st.write(f"📍 {addr}")
            if operator:
                st.write(f"🏢 {operator}")
            h = get_opening_hours(sid)
            if h:
                today = DAYS[datetime.now().weekday()]
                val = h.get(today, "")
                if val:
                    st.write(f"🕐 Aujourd'hui : {val}")
            a = avail_grp.get(sid, {})
            for f_code, f_name in FUELS:
                stt = a.get(f_code, {}).get("status")
                if stt:
                    st.write(f"{status_emoji(stt)} {f_name} : {status_label(stt)}")

    st.divider()

    # ── Carte ──────────────────────────────────────────────────────────────────
    col1, col2 = st.columns([2, 1], gap="large")

    with col1:
        st.subheader("Carte interactive")
        st.caption("Cliquez sur un marqueur ⛽ pour voir les détails de la station.")
        m = build_map(stations, avail_grp, highlight_sid=highlight_sid)
        st_folium(m, width=None, height=560, returned_objects=[])

    with col2:
        st.subheader("Tableau des disponibilités")
        df = stations.merge(avail, left_on="id", right_on="station_id", how="left")
        df_view = df.copy()
        if fuel_filter != "(tous)":
            df_view = df_view[df_view["fuel"] == fuel_filter]
        if status_filter != "(tous)":
            df_view = df_view[df_view["status"] == status_filter]

        if not df_view.empty:
            show = df_view[["name","fuel","status","updated_at","updated_by"]].copy()
            show["fuel"] = show["fuel"].map(dict(FUELS))
            show["status"] = show["status"].map(dict(STATUSES))
            show.columns = ["Station","Carburant","Statut","Mis à jour","Par"]
            st.dataframe(show.dropna(subset=["Statut"]), use_container_width=True, height=520)
        else:
            st.info("Aucune donnée selon les filtres.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE : ANNONCES
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📢 Annonces":
    st.title("📢 CarbuRIM — Annonces des stations")
    ann = get_announcements(active_only=True)
    if ann.empty:
        st.info("Aucune annonce publiée pour le moment.")
    else:
        all_st = ["Toutes les stations"] + ann["station_name"].unique().tolist()
        f_st = st.selectbox("Filtrer par station", all_st)
        if f_st != "Toutes les stations":
            ann = ann[ann["station_name"] == f_st]
        for _, row in ann.iterrows():
            dt = row["published_at"][:10] if row["published_at"] else ""
            with st.container(border=True):
                st.markdown(f"**{CATEGORIES.get(row['category'],row['category'])} — {row['title']}**")
                st.markdown(f"🏪 *{row['station_name']}* &nbsp;|&nbsp; 📅 {dt}")
                st.markdown(row["body"])


# ══════════════════════════════════════════════════════════════════════════════
# PAGE : ESPACE STATION
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🔐 Espace station":
    if not st.session_state.get("station_logged_in"):
        st.title("🔐 Espace station — Connexion")
        st.markdown("Connectez-vous avec les identifiants fournis par votre administrateur.")
        with st.form("login_form"):
            username = st.text_input("Identifiant")
            password = st.text_input("Mot de passe", type="password")
            submitted = st.form_submit_button("Se connecter")
        if submitted:
            sid = authenticate(username, password)
            if sid:
                with db() as con:
                    row = con.execute("SELECT name FROM stations WHERE id=?", (sid,)).fetchone()
                st.session_state["station_logged_in"] = True
                st.session_state["station_id"] = sid
                st.session_state["station_name"] = row[0] if row else f"Station #{sid}"
                st.rerun()
            else:
                st.error("Identifiant ou mot de passe incorrect.")
    else:
        sid = st.session_state["station_id"]
        sname = st.session_state["station_name"]
        st.title(f"🏪 {sname}")
        _, col_logout = st.columns([4, 1])
        with col_logout:
            if st.button("🚪 Déconnexion"):
                logout(); st.rerun()

        tab1, tab2, tab3 = st.tabs(["⛽ Carburants", "📢 Annonces", "🕐 Horaires"])

        with tab1:
            st.subheader("Mettre à jour les disponibilités")
            avail_current = get_availability()
            avail_s = avail_current[avail_current["station_id"]==sid] if not avail_current.empty else pd.DataFrame()
            avail_dict = avail_s.set_index("fuel")["status"].to_dict() if not avail_s.empty else {}
            status_options = [s for s, _ in STATUSES]
            with st.form("fuel_form"):
                new_statuses = {}
                for f_code, f_name in FUELS:
                    current = avail_dict.get(f_code, "INCERTAIN")
                    idx = status_options.index(current) if current in status_options else 2
                    cf, cs = st.columns([2, 3])
                    with cf: st.markdown(f"**{f_name}**")
                    with cs:
                        new_statuses[f_code] = st.radio(f_name, status_options,
                            index=idx, format_func=status_label,
                            horizontal=True, label_visibility="collapsed", key=f"f_{f_code}")
                if st.form_submit_button("💾 Enregistrer"):
                    for f_code, f_status in new_statuses.items():
                        set_status(sid, f_code, f_status, updated_by=sname)
                    st.success("✅ Disponibilités mises à jour !")
                    st.rerun()

            st.divider()
            st.markdown("**État actuel :**")
            fresh = get_availability()
            avail_now = fresh[fresh["station_id"]==sid] if not fresh.empty else pd.DataFrame()
            if not avail_now.empty:
                for _, row in avail_now.iterrows():
                    ca, cb, cc = st.columns([2,2,3])
                    ca.write(fuel_label(row["fuel"]))
                    cb.write(status_label(row["status"]))
                    upd = row.get("updated_at","")
                    cc.caption(f"Mis à jour : {upd[:16].replace('T',' ')} UTC" if upd else "")
            else:
                st.info("Aucun statut renseigné.")

        with tab2:
            st.subheader("Publier une annonce")
            with st.form("ann_form"):
                ann_cat = st.selectbox("Catégorie", list(CATEGORIES.keys()), format_func=lambda k: CATEGORIES[k])
                ann_title = st.text_input("Titre")
                ann_body = st.text_area("Contenu", height=100)
                if st.form_submit_button("📢 Publier"):
                    if ann_title.strip() and ann_body.strip():
                        add_announcement(sid, ann_title.strip(), ann_body.strip(), ann_cat)
                        st.success("Annonce publiée !"); st.rerun()
                    else:
                        st.warning("Titre et contenu obligatoires.")
            st.divider()
            st.subheader("Mes annonces")
            my_ann = get_announcements(station_id=sid, active_only=False)
            if my_ann.empty:
                st.info("Aucune annonce.")
            else:
                for _, row in my_ann.iterrows():
                    active = bool(row["active"])
                    dt = row["published_at"][:16].replace("T"," ") if row["published_at"] else ""
                    with st.container(border=True):
                        c1, c2, c3 = st.columns([4,1,1])
                        with c1:
                            st.markdown(f"**{CATEGORIES.get(row['category'],row['category'])} — {row['title']}** {'🟢' if active else '🔴'}")
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
                hours_input = {}
                cols = st.columns(2)
                for i, day in enumerate(DAYS):
                    with cols[i % 2]:
                        hours_input[day] = st.text_input(day.capitalize(),
                            value=existing.get(day,""), placeholder="ex: 07:00–22:00")
                note = st.text_area("Note", value=existing.get("note",""))
                if st.form_submit_button("💾 Enregistrer"):
                    save_opening_hours(sid, hours_input, note)
                    st.success("✅ Horaires enregistrés !"); st.rerun()
            h = get_opening_hours(sid)
            if h:
                st.divider()
                for day in DAYS:
                    if h.get(day):
                        st.write(f"**{day.capitalize()}** : {h[day]}")
                if h.get("note"):
                    st.info(h["note"])


# ══════════════════════════════════════════════════════════════════════════════
# PAGE : ADMINISTRATION
# ══════════════════════════════════════════════════════════════════════════════
elif page == "⚙️ Administration":
    st.title("⚙️ CarbuRIM — Administration")
    try:
        ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin1234")
    except Exception:
        ADMIN_PASSWORD = "admin1234"

    if not st.session_state.get("admin_logged_in"):
        st.markdown("Accès réservé à l'administrateur.")
        with st.form("admin_login"):
            pwd = st.text_input("Mot de passe", type="password")
            if st.form_submit_button("Accéder"):
                if pwd == ADMIN_PASSWORD:
                    st.session_state["admin_logged_in"] = True; st.rerun()
                else:
                    st.error("Mot de passe incorrect.")
    else:
        if st.button("🚪 Déconnexion admin"):
            st.session_state.pop("admin_logged_in", None); st.rerun()

        tab_a, tab_b, tab_c = st.tabs(["👤 Créer un compte", "📋 Comptes existants", "🔄 OSM"])

        with tab_a:
            st.subheader("Créer un compte station")
            if stations.empty:
                st.warning("Aucune station. Actualisez d'abord depuis OSM.")
            else:
                station_opts = stations.apply(lambda r: f"{r['name']} (id={r['id']})", axis=1).tolist()
                with st.form("create_account_form"):
                    s_choice = st.selectbox("Station", station_opts)
                    new_user = st.text_input("Identifiant")
                    new_pass = st.text_input("Mot de passe", type="password")
                    new_pass2 = st.text_input("Confirmer", type="password")
                    if st.form_submit_button("Créer"):
                        s_id = int(s_choice.split("id=")[1].replace(")",""))
                        if not new_user.strip():
                            st.error("Identifiant vide.")
                        elif new_pass != new_pass2:
                            st.error("Mots de passe différents.")
                        elif len(new_pass) < 6:
                            st.error("Minimum 6 caractères.")
                        elif create_account(s_id, new_user, new_pass):
                            st.success(f"✅ Compte créé pour {s_choice.split(' (id=')[0]}.")
                        else:
                            st.error("Identifiant ou station déjà utilisé.")

        with tab_b:
            st.subheader("Comptes existants")
            accounts = list_accounts()
            if accounts.empty:
                st.info("Aucun compte.")
            else:
                accounts.columns = ["ID","Identifiant","Station","Créé le"]
                st.dataframe(accounts, use_container_width=True)

        with tab_c:
            st.subheader("Actualiser depuis OSM")
            if st.button("🔄 Lancer l'import"):
                with st.spinner("Récupération…"):
                    df_osm = overpass_fetch_stations()
                    upsert_stations(df_osm)
                st.success(f"✅ {len(df_osm)} stations importées.")
                st.rerun()
