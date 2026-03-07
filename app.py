import hashlib
import sqlite3
import time
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
import folium
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


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def status_label(code: str) -> str:
    return dict(STATUSES).get(code, code)

def fuel_label(code: str) -> str:
    return dict(FUELS).get(code, code)

def status_emoji(code: str) -> str:
    return {"DISPONIBLE": "✅", "RUPTURE": "❌", "INCERTAIN": "❓"}.get(code, "•")


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

    df = pd.DataFrame(rows).drop_duplicates(subset=["osm_id"])
    return df


# ─── DATABASE ─────────────────────────────────────────────────────────────────

def db():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    with db() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS stations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            osm_id TEXT UNIQUE,
            name TEXT,
            operator TEXT,
            lat REAL,
            lon REAL,
            address TEXT
        )""")

        con.execute("""
        CREATE TABLE IF NOT EXISTS availability (
            station_id INTEGER,
            fuel TEXT,
            status TEXT,
            updated_at TEXT,
            updated_by TEXT,
            PRIMARY KEY (station_id, fuel),
            FOREIGN KEY (station_id) REFERENCES stations(id)
        )""")

        # Comptes station (espace sécurisé)
        con.execute("""
        CREATE TABLE IF NOT EXISTS station_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id INTEGER UNIQUE,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT,
            FOREIGN KEY (station_id) REFERENCES stations(id)
        )""")

        # Annonces publiées par les stations
        con.execute("""
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id INTEGER,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            category TEXT DEFAULT 'INFO',
            published_at TEXT,
            active INTEGER DEFAULT 1,
            FOREIGN KEY (station_id) REFERENCES stations(id)
        )""")

        # Horaires d'ouverture
        con.execute("""
        CREATE TABLE IF NOT EXISTS opening_hours (
            station_id INTEGER PRIMARY KEY,
            lundi TEXT, mardi TEXT, mercredi TEXT,
            jeudi TEXT, vendredi TEXT, samedi TEXT, dimanche TEXT,
            note TEXT,
            updated_at TEXT,
            FOREIGN KEY (station_id) REFERENCES stations(id)
        )""")

def upsert_stations(df: pd.DataFrame):
    with db() as con:
        for _, row in df.iterrows():
            con.execute("""
            INSERT INTO stations (osm_id, name, operator, lat, lon, address)
            VALUES (?, ?, ?, ?, ?, ?)
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

def set_status(station_id: int, fuel: str, status: str, updated_by: str = "station"):
    now = datetime.now(timezone.utc).isoformat()
    with db() as con:
        con.execute("""
        INSERT INTO availability (station_id, fuel, status, updated_at, updated_by)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(station_id, fuel) DO UPDATE SET
            status=excluded.status, updated_at=excluded.updated_at,
            updated_by=excluded.updated_by
        """, (station_id, fuel, status, now, updated_by))

# ─── ACCOUNTS ─────────────────────────────────────────────────────────────────

def create_account(station_id: int, username: str, password: str) -> bool:
    try:
        now = datetime.now(timezone.utc).isoformat()
        with db() as con:
            con.execute("""
            INSERT INTO station_accounts (station_id, username, password_hash, created_at)
            VALUES (?, ?, ?, ?)
            """, (station_id, username.strip(), hash_password(password), now))
        return True
    except sqlite3.IntegrityError:
        return False

def authenticate(username: str, password: str):
    """Returns station_id if credentials are valid, else None."""
    with db() as con:
        row = con.execute("""
        SELECT station_id FROM station_accounts
        WHERE username=? AND password_hash=?
        """, (username.strip(), hash_password(password))).fetchone()
    return row[0] if row else None

def get_account_for_station(station_id: int):
    with db() as con:
        return con.execute(
            "SELECT username FROM station_accounts WHERE station_id=?",
            (station_id,)
        ).fetchone()

def list_accounts() -> pd.DataFrame:
    with db() as con:
        return pd.read_sql_query("""
            SELECT sa.id, sa.username, s.name AS station_name, sa.created_at
            FROM station_accounts sa
            JOIN stations s ON s.id = sa.station_id
        """, con)

# ─── ANNOUNCEMENTS ────────────────────────────────────────────────────────────

CATEGORIES = {
    "INFO": "ℹ️ Information",
    "PROMO": "🎁 Promotion",
    "ALERTE": "⚠️ Alerte",
    "SERVICE": "🔧 Service",
}

def add_announcement(station_id: int, title: str, body: str, category: str = "INFO"):
    now = datetime.now(timezone.utc).isoformat()
    with db() as con:
        con.execute("""
        INSERT INTO announcements (station_id, title, body, category, published_at, active)
        VALUES (?, ?, ?, ?, ?, 1)
        """, (station_id, title, body, category, now))

def get_announcements(station_id: int = None, active_only: bool = True) -> pd.DataFrame:
    with db() as con:
        q = """
        SELECT a.id, a.station_id, s.name AS station_name,
               a.title, a.body, a.category, a.published_at, a.active
        FROM announcements a
        JOIN stations s ON s.id = a.station_id
        """
        params = []
        filters = []
        if station_id:
            filters.append("a.station_id = ?")
            params.append(station_id)
        if active_only:
            filters.append("a.active = 1")
        if filters:
            q += " WHERE " + " AND ".join(filters)
        q += " ORDER BY a.published_at DESC"
        return pd.read_sql_query(q, con, params=params)

def toggle_announcement(ann_id: int, active: int):
    with db() as con:
        con.execute("UPDATE announcements SET active=? WHERE id=?", (active, ann_id))

def delete_announcement(ann_id: int):
    with db() as con:
        con.execute("DELETE FROM announcements WHERE id=?", (ann_id,))

# ─── OPENING HOURS ────────────────────────────────────────────────────────────

DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

def save_opening_hours(station_id: int, hours: dict, note: str):
    now = datetime.now(timezone.utc).isoformat()
    with db() as con:
        con.execute("""
        INSERT INTO opening_hours (station_id, lundi, mardi, mercredi, jeudi, vendredi,
            samedi, dimanche, note, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(station_id) DO UPDATE SET
            lundi=excluded.lundi, mardi=excluded.mardi, mercredi=excluded.mercredi,
            jeudi=excluded.jeudi, vendredi=excluded.vendredi, samedi=excluded.samedi,
            dimanche=excluded.dimanche, note=excluded.note, updated_at=excluded.updated_at
        """, (station_id, hours.get("lundi",""), hours.get("mardi",""),
              hours.get("mercredi",""), hours.get("jeudi",""), hours.get("vendredi",""),
              hours.get("samedi",""), hours.get("dimanche",""), note, now))

def get_opening_hours(station_id: int) -> dict:
    with db() as con:
        row = con.execute(
            "SELECT * FROM opening_hours WHERE station_id=?", (station_id,)
        ).fetchone()
        if row:
            cols = [d[0] for d in con.execute("PRAGMA table_info(opening_hours)").fetchall()]
            return dict(zip(cols, row))
    return {}


# ─── SESSION STATE ─────────────────────────────────────────────────────────────

def logout():
    st.session_state.pop("station_logged_in", None)
    st.session_state.pop("station_id", None)
    st.session_state.pop("station_name", None)


# ══════════════════════════════════════════════════════════════════════════════
# APP
# ══════════════════════════════════════════════════════════════════════════════

st.set_page_config(page_title="CarbuRIM", page_icon="⛽", layout="wide")
init_db()

stations = get_stations()

# ─── SIDEBAR ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/4/43/Flag_of_Mauritania.svg/320px-Flag_of_Mauritania.svg.png", width=80)
    st.title("⛽ CarbuRIM")

    page = st.radio(
        "Navigation",
        ["🗺️ Carte publique", "📢 Annonces", "🔐 Espace station", "⚙️ Administration"],
        label_visibility="collapsed"
    )
    st.divider()

    if page == "🗺️ Carte publique":
        st.header("Filtres")
        fuel_filter = st.selectbox("Carburant", ["(tous)"] + [f for f, _ in FUELS])
        status_filter = st.selectbox("Statut", ["(tous)"] + [s for s, _ in STATUSES])
        st.divider()
        if st.button("🔄 Actualiser depuis OSM"):
            with st.spinner("Récupération des stations…"):
                df_osm = overpass_fetch_stations()
                upsert_stations(df_osm)
            st.success(f"{len(df_osm)} stations importées/mises à jour.")
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
    df = stations.merge(avail, left_on="id", right_on="station_id", how="left")

    df_view = df.copy()
    if fuel_filter != "(tous)":
        df_view = df_view[df_view["fuel"] == fuel_filter]
    if status_filter != "(tous)":
        df_view = df_view[df_view["status"] == status_filter]

    center_lat = stations["lat"].mean()
    center_lon = stations["lon"].mean()
    m = folium.Map(location=[center_lat, center_lon], zoom_start=12)

    avail_grp = {}
    if not avail.empty:
        for sid, grp in avail.groupby("station_id"):
            avail_grp[sid] = grp.set_index("fuel")[["status", "updated_at"]].to_dict("index")

    for _, s in stations.iterrows():
        sid = int(s["id"])
        a = avail_grp.get(sid, {})
        lines = []
        for f_code, f_name in FUELS:
            stt = a.get(f_code, {}).get("status")
            upd = a.get(f_code, {}).get("updated_at", "")
            upd_str = f" <small>({upd[:10]})</small>" if upd else ""
            if stt:
                lines.append(f"{status_emoji(stt)} {f_name}: {status_label(stt)}{upd_str}")
            else:
                lines.append(f"• {f_name}: <i>non renseigné</i>")

        # annonces actives
        ann = get_announcements(station_id=sid, active_only=True)
        ann_html = ""
        if not ann.empty:
            ann_html = "<hr/><b>📢 Annonces :</b><br/>"
            for _, row in ann.iterrows():
                cat_icon = CATEGORIES.get(row["category"], "ℹ️").split()[0]
                ann_html += f"<b>{cat_icon} {row['title']}</b><br/><small>{row['body'][:80]}{'…' if len(row['body'])>80 else ''}</small><br/>"

        popup_html = f"""
        <b style='font-size:14px'>{s['name']}</b><br/>
        <i>{s.get('operator') or ''}</i><br/>
        {s.get('address') or ''}<br/><hr/>
        """ + "<br/>".join(lines) + ann_html

        folium.Marker(
            location=[s["lat"], s["lon"]],
            popup=folium.Popup(popup_html, max_width=380),
            tooltip=s["name"],
        ).add_to(m)

    col1, col2 = st.columns([2, 1], gap="large")

    with col1:
        st.subheader("Carte")
        st_folium(m, width=900, height=600)

    with col2:
        st.subheader("Tableau des disponibilités")
        if not df_view.empty:
            show = df_view[["name", "fuel", "status", "updated_at", "updated_by"]].copy()
            show["fuel"] = show["fuel"].map(dict(FUELS))
            show["status"] = show["status"].map(dict(STATUSES))
            show.columns = ["Station", "Carburant", "Statut", "Mis à jour", "Par"]
            st.dataframe(show.dropna(subset=["Statut"]), use_container_width=True, height=300)
        else:
            st.info("Aucune donnée selon les filtres.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE : ANNONCES PUBLIQUES
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📢 Annonces":
    st.title("📢 CarbuRIM — Annonces des stations")

    ann = get_announcements(active_only=True)

    if ann.empty:
        st.info("Aucune annonce publiée pour le moment.")
    else:
        # Filtre station
        all_stations = ["Toutes les stations"] + ann["station_name"].unique().tolist()
        f_station = st.selectbox("Filtrer par station", all_stations)
        if f_station != "Toutes les stations":
            ann = ann[ann["station_name"] == f_station]

        for _, row in ann.iterrows():
            cat_label = CATEGORIES.get(row["category"], row["category"])
            dt = row["published_at"][:10] if row["published_at"] else ""
            with st.container(border=True):
                st.markdown(f"**{cat_label} — {row['title']}**")
                st.markdown(f"🏪 *{row['station_name']}* &nbsp;|&nbsp; 📅 {dt}")
                st.markdown(row["body"])


# ══════════════════════════════════════════════════════════════════════════════
# PAGE : ESPACE STATION (SÉCURISÉ)
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🔐 Espace station":

    # ── Pas connecté ──────────────────────────────────────────────────────────
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
                # récupérer le nom de la station
                with db() as con:
                    row = con.execute("SELECT name FROM stations WHERE id=?", (sid,)).fetchone()
                st.session_state["station_logged_in"] = True
                st.session_state["station_id"] = sid
                st.session_state["station_name"] = row[0] if row else f"Station #{sid}"
                st.rerun()
            else:
                st.error("Identifiant ou mot de passe incorrect.")

    # ── Connecté ──────────────────────────────────────────────────────────────
    else:
        sid = st.session_state["station_id"]
        sname = st.session_state["station_name"]

        st.title(f"🏪 {sname}")
        col_info, col_logout = st.columns([4, 1])
        with col_logout:
            if st.button("🚪 Déconnexion"):
                logout()
                st.rerun()

        tab1, tab2, tab3 = st.tabs(["⛽ Carburants", "📢 Annonces", "🕐 Horaires"])

        # ── Tab 1 : Carburants ───────────────────────────────────────────────
        with tab1:
            st.subheader("Mettre à jour les disponibilités carburant")

            avail_current = get_availability()
            avail_station = avail_current[avail_current["station_id"] == sid] if not avail_current.empty else pd.DataFrame()
            avail_dict = avail_station.set_index("fuel")["status"].to_dict() if not avail_station.empty else {}

            st.markdown("Sélectionnez le statut actuel pour chaque carburant :")

            status_options = [s for s, _ in STATUSES]

            with st.form("fuel_form"):
                new_statuses = {}
                for f_code, f_name in FUELS:
                    current = avail_dict.get(f_code, "INCERTAIN")
                    idx = status_options.index(current) if current in status_options else 2
                    col_f, col_s = st.columns([2, 3])
                    with col_f:
                        st.markdown(f"**{f_name}**")
                    with col_s:
                        new_statuses[f_code] = st.radio(
                            f_name, status_options,
                            index=idx,
                            format_func=status_label,
                            horizontal=True,
                            label_visibility="collapsed",
                            key=f"fuel_{f_code}"
                        )
                save_fuels = st.form_submit_button("💾 Enregistrer les disponibilités")

            if save_fuels:
                for f_code, f_status in new_statuses.items():
                    set_status(sid, f_code, f_status, updated_by=sname)
                st.success("✅ Disponibilités mises à jour avec succès !")
                st.rerun()

            # Résumé actuel
            st.divider()
            st.markdown("**État actuel enregistré :**")
            avail_station_fresh = get_availability()
            avail_s = avail_station_fresh[avail_station_fresh["station_id"] == sid] if not avail_station_fresh.empty else pd.DataFrame()
            if not avail_s.empty:
                for _, row in avail_s.iterrows():
                    col_a, col_b, col_c = st.columns([2, 2, 3])
                    with col_a:
                        st.write(fuel_label(row["fuel"]))
                    with col_b:
                        st.write(status_label(row["status"]))
                    with col_c:
                        upd = row.get("updated_at", "")
                        st.caption(f"Mis à jour : {upd[:16].replace('T',' ')} UTC" if upd else "")
            else:
                st.info("Aucun statut renseigné.")

        # ── Tab 2 : Annonces ─────────────────────────────────────────────────
        with tab2:
            st.subheader("Publier une annonce")

            with st.form("ann_form"):
                ann_cat = st.selectbox("Catégorie", list(CATEGORIES.keys()),
                                       format_func=lambda k: CATEGORIES[k])
                ann_title = st.text_input("Titre de l'annonce")
                ann_body = st.text_area("Contenu", height=120,
                                        placeholder="Décrivez l'information, la promotion ou l'alerte…")
                pub = st.form_submit_button("📢 Publier")

            if pub:
                if ann_title.strip() and ann_body.strip():
                    add_announcement(sid, ann_title.strip(), ann_body.strip(), ann_cat)
                    st.success("Annonce publiée !")
                    st.rerun()
                else:
                    st.warning("Veuillez remplir le titre et le contenu.")

            st.divider()
            st.subheader("Mes annonces")

            my_ann = get_announcements(station_id=sid, active_only=False)
            if my_ann.empty:
                st.info("Aucune annonce publiée.")
            else:
                for _, row in my_ann.iterrows():
                    active = bool(row["active"])
                    cat_label = CATEGORIES.get(row["category"], row["category"])
                    dt = row["published_at"][:16].replace("T", " ") if row["published_at"] else ""
                    with st.container(border=True):
                        c1, c2, c3 = st.columns([4, 1, 1])
                        with c1:
                            status_badge = "🟢 Active" if active else "🔴 Désactivée"
                            st.markdown(f"**{cat_label} — {row['title']}** &nbsp; {status_badge}")
                            st.caption(f"Publiée le {dt}")
                            st.markdown(row["body"])
                        with c2:
                            btn_label = "⏸ Désactiver" if active else "▶️ Réactiver"
                            if st.button(btn_label, key=f"toggle_{row['id']}"):
                                toggle_announcement(row["id"], 0 if active else 1)
                                st.rerun()
                        with c3:
                            if st.button("🗑️ Supprimer", key=f"del_{row['id']}"):
                                delete_announcement(row["id"])
                                st.rerun()

        # ── Tab 3 : Horaires ─────────────────────────────────────────────────
        with tab3:
            st.subheader("Horaires d'ouverture")
            st.caption("Ces horaires seront visibles par les usagers dans la fiche de votre station.")

            existing = get_opening_hours(sid)

            with st.form("hours_form"):
                hours_input = {}
                st.markdown("**Horaires par jour** (ex : 07:00–22:00 ou Fermé)")
                cols = st.columns(2)
                for i, day in enumerate(DAYS):
                    with cols[i % 2]:
                        hours_input[day] = st.text_input(
                            day.capitalize(),
                            value=existing.get(day, ""),
                            placeholder="ex: 07:00–22:00"
                        )
                note = st.text_area("Note / informations complémentaires",
                                    value=existing.get("note", ""),
                                    placeholder="ex: Ouvert les jours fériés, service 24h/24…")
                save_hours = st.form_submit_button("💾 Enregistrer les horaires")

            if save_hours:
                save_opening_hours(sid, hours_input, note)
                st.success("✅ Horaires enregistrés !")
                st.rerun()

            # Résumé
            h = get_opening_hours(sid)
            if h:
                st.divider()
                st.markdown("**Horaires actuellement enregistrés :**")
                for day in DAYS:
                    val = h.get(day, "")
                    if val:
                        st.write(f"**{day.capitalize()}** : {val}")
                if h.get("note"):
                    st.info(h["note"])


# ══════════════════════════════════════════════════════════════════════════════
# PAGE : ADMINISTRATION
# ══════════════════════════════════════════════════════════════════════════════
elif page == "⚙️ Administration":
    st.title("⚙️ CarbuRIM — Administration")

    ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin1234")

    if not st.session_state.get("admin_logged_in"):
        st.markdown("Accès réservé à l'administrateur du système.")
        with st.form("admin_login"):
            pwd = st.text_input("Mot de passe administrateur", type="password")
            ok = st.form_submit_button("Accéder")
        if ok:
            if pwd == ADMIN_PASSWORD:
                st.session_state["admin_logged_in"] = True
                st.rerun()
            else:
                st.error("Mot de passe incorrect.")
    else:
        if st.button("🚪 Déconnexion admin"):
            st.session_state.pop("admin_logged_in", None)
            st.rerun()

        tab_a, tab_b, tab_c = st.tabs(["👤 Créer un compte station", "📋 Comptes existants", "🔄 Actualiser OSM"])

        # ── Tab A : Créer compte ─────────────────────────────────────────────
        with tab_a:
            st.subheader("Créer un compte pour une station")

            if stations.empty:
                st.warning("Aucune station en base. Actualisez d'abord depuis OSM.")
            else:
                # Stations sans compte
                accounts_df = list_accounts()
                if not accounts_df.empty:
                    existing_sids = accounts_df["station_name"].tolist()
                else:
                    existing_sids = []

                station_opts = stations.apply(
                    lambda r: f"{r['name']} (id={r['id']})", axis=1
                ).tolist()

                with st.form("create_account_form"):
                    s_choice = st.selectbox("Station", station_opts)
                    new_user = st.text_input("Identifiant de connexion")
                    new_pass = st.text_input("Mot de passe", type="password")
                    new_pass2 = st.text_input("Confirmer le mot de passe", type="password")
                    create_btn = st.form_submit_button("Créer le compte")

                if create_btn:
                    s_id = int(s_choice.split("id=")[1].replace(")", ""))
                    if not new_user.strip():
                        st.error("L'identifiant ne peut pas être vide.")
                    elif new_pass != new_pass2:
                        st.error("Les mots de passe ne correspondent pas.")
                    elif len(new_pass) < 6:
                        st.error("Le mot de passe doit faire au moins 6 caractères.")
                    else:
                        ok = create_account(s_id, new_user, new_pass)
                        if ok:
                            st.success(f"✅ Compte créé pour la station **{s_choice.split(' (id=')[0]}**.")
                        else:
                            st.error("Ce nom d'utilisateur ou cette station a déjà un compte.")

        # ── Tab B : Comptes existants ────────────────────────────────────────
        with tab_b:
            st.subheader("Comptes stations existants")
            accounts = list_accounts()
            if accounts.empty:
                st.info("Aucun compte créé.")
            else:
                accounts.columns = ["ID", "Identifiant", "Station", "Créé le"]
                st.dataframe(accounts, use_container_width=True)

        # ── Tab C : OSM ──────────────────────────────────────────────────────
        with tab_c:
            st.subheader("Actualiser la liste des stations depuis OSM")
            if st.button("🔄 Lancer l'import OSM"):
                with st.spinner("Récupération en cours…"):
                    df_osm = overpass_fetch_stations()
                    upsert_stations(df_osm)
                st.success(f"✅ {len(df_osm)} stations importées/mises à jour.")
                st.rerun()
