"""
Kolkata & Howrah Rental Finder (PG / Flats)
Idea, development and design: Rik Karmakar.

Data sources (all free / open):
  - OpenStreetMap Nominatim  -> place search (geocoding)
  - OpenStreetMap Overpass   -> nearby PG/hostels/flats, metro stations, metro gates
  - OpenStreetMap tiles      -> map
"""

import html
import math
import urllib.parse

import folium
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium

# ----------------------------------------------------------------------------
# Creator credit
# ----------------------------------------------------------------------------
DEVELOPER_NAME = "Rik Karmakar"

# Kolkata + Howrah bounding box (south, west, north, east)
BBOX = (22.40, 88.20, 22.75, 88.50)
DEFAULT_CENTER = (22.5726, 88.3639)  # Kolkata

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
HEADERS = {"User-Agent": "KolkataHowrahRentalFinder/1.0 (streamlit app)"}

st.set_page_config(
    page_title="Kolkata & Howrah Rental Finder",
    page_icon="🏠",
    layout="wide",
)

CSS = """
<style>
.block-container {padding-top: 1.2rem; max-width: 1300px;}
.hero {
    background: linear-gradient(120deg, #0f2027 0%, #203a43 45%, #2c7a7b 100%);
    border-radius: 18px; padding: 28px 32px; color: #fff; margin-bottom: 18px;
    box-shadow: 0 8px 24px rgba(0,0,0,.18);
}
.hero h1 {margin: 0; font-size: 2.1rem; font-weight: 800; letter-spacing: .3px; color:#fff;}
.hero p {margin: 6px 0 0 0; font-size: 1.02rem; opacity: .92;}
.hero .credit {
    display: inline-block; margin-top: 14px; padding: 6px 14px; border-radius: 999px;
    background: rgba(255,255,255,.15); font-size: .9rem; backdrop-filter: blur(4px);
}
.chips span {
    display:inline-block; margin: 10px 8px 0 0; padding: 4px 12px; border-radius: 999px;
    background: rgba(255,255,255,.18); font-size: .82rem;
}
.metric-card {
    background: #ffffff; border-radius: 14px; padding: 14px 18px; text-align: center;
    border: 1px solid #e6ecef; box-shadow: 0 2px 10px rgba(0,0,0,.05);
}
.metric-card .num {font-size: 1.8rem; font-weight: 800; color: #1f6f78;}
.metric-card .lbl {font-size: .85rem; color: #5b6b73;}
.legend {
    display:flex; flex-wrap:wrap; gap:10px; margin: 8px 0 14px 0; font-size:.85rem;
}
.legend span {background:#f3f7f8; border:1px solid #e1e9ec; border-radius:999px; padding:4px 12px; color:#33444c;}
.footer {
    text-align:center; margin-top: 28px; padding: 16px; border-radius: 14px;
    background: linear-gradient(120deg, #0f2027, #2c7a7b); color: #fff; font-size: .95rem;
}
.footer b {letter-spacing:.3px;}
section[data-testid="stSidebar"] {background: #f4f8f9;}
div[data-testid="stTabs"] button {font-weight: 600;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def haversine_m(lat1, lon1, lat2, lon2):
    """Distance in metres between two lat/lon points."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def esc(value):
    return html.escape(str(value)) if value is not None else ""


@st.cache_data(show_spinner=False, ttl=3600)
def geocode(query):
    """Search a place inside the Kolkata/Howrah area. Returns list of dicts."""
    south, west, north, east = BBOX
    results = []
    for q in (query, f"{query}, Kolkata", f"{query}, Howrah"):
        params = {
            "q": q,
            "format": "json",
            "limit": 6,
            "viewbox": f"{west},{north},{east},{south}",
            "bounded": 1,
            "countrycode": "in",
        }
        try:
            r = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception:
            data = []
        for item in data:
            try:
                results.append(
                    {
                        "name": item.get("display_name", "Unknown"),
                        "lat": float(item["lat"]),
                        "lon": float(item["lon"]),
                    }
                )
            except (KeyError, ValueError):
                continue
        if results:
            break
    # de-duplicate
    seen, unique = set(), []
    for r_ in results:
        key = (round(r_["lat"], 5), round(r_["lon"], 5))
        if key not in seen:
            seen.add(key)
            unique.append(r_)
    return unique


def run_overpass(query):
    """Try several Overpass servers; return elements list or raise."""
    last_err = None
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data={"data": query}, headers=HEADERS, timeout=60)
            if r.status_code == 200:
                return r.json().get("elements", [])
            last_err = f"{url} returned {r.status_code}"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
    raise RuntimeError(last_err or "Overpass unavailable")


@st.cache_data(show_spinner=False, ttl=1800)
def fetch_stays(lat, lon, radius):
    q = f"""
    [out:json][timeout:50];
    (
      nwr(around:{radius},{lat},{lon})["tourism"~"^(hostel|guest_house|apartment)$"];
      nwr(around:{radius},{lat},{lon})["amenity"="hostel"];
      nwr(around:{radius},{lat},{lon})["building"~"^(apartments|dormitory)$"];
      nwr(around:{radius},{lat},{lon})["social_facility"="shelter"]["name"~"PG|Hostel|Mess|Ladies|Boys|Girls",i];
      nwr(around:{radius},{lat},{lon})["name"~"PG|Paying Guest",i]["building"];
    );
    out center tags 250;
    """
    elements = run_overpass(q)
    rows, seen = [], set()
    for el in elements:
        tags = el.get("tags", {})
        if "lat" in el:
            plat, plon = el["lat"], el["lon"]
        elif "center" in el:
            plat, plon = el["center"]["lat"], el["center"]["lon"]
        else:
            continue
        name_l = (tags.get("name") or "").lower()
        if (
            tags.get("tourism") == "hostel"
            or tags.get("amenity") == "hostel"
            or tags.get("building") == "dormitory"
            or " pg" in f" {name_l} "
            or "paying guest" in name_l
            or "hostel" in name_l
            or "mess" in name_l
        ):
            kind = "PG / Hostel"
        elif tags.get("tourism") == "guest_house":
            kind = "Guest house"
        else:
            kind = "Flat / Apartment"
        name = tags.get("name") or f"Unnamed {kind.lower()}"
        key = (name, round(plat, 5), round(plon, 5))
        if key in seen:
            continue
        seen.add(key)
        addr_parts = [
            tags.get("addr:housenumber"),
            tags.get("addr:street"),
            tags.get("addr:suburb") or tags.get("addr:neighbourhood"),
            tags.get("addr:city"),
            tags.get("addr:postcode"),
        ]
        address = ", ".join(p for p in addr_parts if p)
        rows.append(
            {
                "Name": name,
                "Type": kind,
                "Address": address or "-",
                "Phone": tags.get("phone") or tags.get("contact:phone") or "-",
                "Website": tags.get("website") or tags.get("contact:website") or "",
                "lat": plat,
                "lon": plon,
                "Distance (m)": int(haversine_m(lat, lon, plat, plon)),
            }
        )
    return rows


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_metro_full(lat, lon, radius):
    q = f"""
    [out:json][timeout:50];
    (
      node(around:{radius},{lat},{lon})["railway"="station"]["station"="subway"];
      node(around:{radius},{lat},{lon})["railway"="station"]["network"~"Kolkata Metro",i];
      node(around:{radius},{lat},{lon})["railway"="subway_entrance"];
    );
    out body 400;
    """
    elements = run_overpass(q)
    stations, gates, seen = [], [], set()
    for el in elements:
        tags = el.get("tags", {})
        if "lat" not in el:
            continue
        la, lo = el["lat"], el["lon"]
        if tags.get("railway") == "subway_entrance":
            ref = tags.get("ref") or tags.get("entrance:ref") or ""
            nm = tags.get("name") or ""
            label = f"Gate {ref}" if ref else (nm or "Gate (number not tagged on OSM)")
            gates.append(
                {
                    "Gate": label,
                    "Gate No.": ref or "-",
                    "Name": nm or "-",
                    "lat": la,
                    "lon": lo,
                    "Distance (m)": int(haversine_m(lat, lon, la, lo)),
                }
            )
        else:
            nm = tags.get("name") or tags.get("name:en") or "Metro station"
            key = (nm, round(la, 4), round(lo, 4))
            if key in seen:
                continue
            seen.add(key)
            stations.append(
                {
                    "Station": nm,
                    "lat": la,
                    "lon": lo,
                    "Distance (m)": int(haversine_m(lat, lon, la, lo)),
                }
            )
    stations.sort(key=lambda x: x["Distance (m)"])
    gates.sort(key=lambda x: x["Distance (m)"])
    return stations, gates


def nearest_station(plat, plon, stations):
    if not stations:
        return "-", None
    best = min(stations, key=lambda s: haversine_m(plat, plon, s["lat"], s["lon"]))
    return best["Station"], int(haversine_m(plat, plon, best["lat"], best["lon"]))


def search_links(place, kind_word, months):
    base = f"{kind_word} for rent {months} months near {place}"
    links = {}
    sites = {
        "NoBroker": "nobroker.in",
        "MagicBricks": "magicbricks.com",
        "99acres": "99acres.com",
        "Housing.com": "housing.com",
        "NestAway": "nestaway.com",
        "Stanza Living": "stanzaliving.com",
    }
    for label, domain in sites.items():
        q = urllib.parse.quote_plus(f"{base} site:{domain}")
        links[label] = f"https://www.google.com/search?q={q}"
    links["Google Maps"] = "https://www.google.com/maps/search/" + urllib.parse.quote_plus(
        f"{kind_word} near {place}"
    )
    return links


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
st.markdown(
    f"""
    <div class="hero">
      <h1>🏠 Kolkata &amp; Howrah Rental Finder</h1>
      <p>Find nearby PGs, hostels and flats for 3, 6 or 12 months, with metro stations and gate numbers on the map.</p>
      <div class="chips"><span>🗺️ Live OpenStreetMap data</span><span>🚇 Metro stations &amp; gates</span><span>🏘️ PG &amp; Flats</span></div>
      <div class="credit">✨ Idea, development &amp; design by <b>{esc(DEVELOPER_NAME)}</b></div>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Search settings")
    place = st.text_input(
        "Enter location",
        placeholder="e.g. Salt Lake Sector V, Howrah Station, Jadavpur",
    )
    duration = st.radio("Rental duration", [3, 6, 12], index=1, format_func=lambda m: f"{m} months")
    radius = st.slider("Search radius (metres)", 300, 5000, 1500, step=100)
    metro_radius = st.slider("Metro search radius (metres)", 500, 5000, 3000, step=250)
    types = st.multiselect(
        "Show",
        ["PG / Hostel", "Flat / Apartment", "Guest house"],
        default=["PG / Hostel", "Flat / Apartment", "Guest house"],
    )
    show_metro = st.checkbox("Show metro stations & gates", value=True)
    use_manual = st.checkbox("Enter latitude/longitude manually")
    mlat = mlon = None
    if use_manual:
        mlat = st.number_input("Latitude", value=DEFAULT_CENTER[0], format="%.6f")
        mlon = st.number_input("Longitude", value=DEFAULT_CENTER[1], format="%.6f")
    go = st.button("🔍 Search", type="primary", use_container_width=True)

if "state" not in st.session_state:
    st.session_state.state = None

if go:
    chosen = None
    if use_manual:
        chosen = {"name": f"{mlat:.5f}, {mlon:.5f}", "lat": mlat, "lon": mlon}
        st.session_state.candidates = None
    elif not place.strip():
        st.warning("Please enter a location first.")
    else:
        with st.spinner("Finding location..."):
            cands = geocode(place.strip())
        if not cands:
            st.error(
                "Location not found inside Kolkata/Howrah. Try a nearby landmark, "
                "a different spelling, or use manual latitude/longitude."
            )
        else:
            st.session_state.candidates = cands
            chosen = cands[0]
    if chosen:
        st.session_state.chosen = chosen
        st.session_state.params = (radius, metro_radius, duration)
        st.session_state.state = "run"

if st.session_state.get("state") == "run" and st.session_state.get("chosen"):
    cands = st.session_state.get("candidates")
    chosen = st.session_state.chosen
    if cands and len(cands) > 1:
        labels = [c["name"] for c in cands]
        idx = st.selectbox(
            "Multiple matches found - pick the right one:",
            range(len(labels)),
            format_func=lambda i: labels[i],
            index=min(labels.index(chosen["name"]) if chosen["name"] in labels else 0, len(labels) - 1),
        )
        chosen = cands[idx]

    lat, lon = chosen["lat"], chosen["lon"]
    st.success(f"📍 {chosen['name']}")

    stays, stations, gates = [], [], []
    err = None
    with st.spinner("Fetching nearby PGs, flats and metro data from OpenStreetMap..."):
        try:
            stays = fetch_stays(round(lat, 5), round(lon, 5), radius)
        except Exception as e:  # noqa: BLE001
            err = f"Could not load rentals data: {e}"
        if show_metro:
            try:
                stations, gates = fetch_metro_full(round(lat, 5), round(lon, 5), metro_radius)
            except Exception as e:  # noqa: BLE001
                err = (err + " | " if err else "") + f"Could not load metro data: {e}"
    if err:
        st.warning(err + " (public OpenStreetMap servers can be busy - try again in a moment.)")

    stays = [s for s in stays if s["Type"] in types]
    for s in stays:
        st_name, st_dist = nearest_station(s["lat"], s["lon"], stations)
        s["Nearest Metro"] = st_name
        s["Metro Dist (m)"] = st_dist if st_dist is not None else "-"
    stays.sort(key=lambda x: x["Distance (m)"])

    # ---- Summary cards ----
    n_pg = sum(1 for x in stays if x["Type"] == "PG / Hostel")
    n_fl = sum(1 for x in stays if x["Type"] == "Flat / Apartment")
    cards = [
        (len(stays), "Places found"),
        (n_pg, "PG / Hostels"),
        (n_fl, "Flats / Apartments"),
        (len(stations), "Metro stations"),
        (len(gates), "Metro gates"),
    ]
    for col, (num, lbl) in zip(st.columns(5), cards):
        col.markdown(
            f'<div class="metric-card"><div class="num">{num}</div><div class="lbl">{lbl}</div></div>',
            unsafe_allow_html=True,
        )
    st.write("")

    # ---- Map ----
    m = folium.Map(location=[lat, lon], zoom_start=15, tiles="CartoDB positron", control_scale=True)
    s_, w_, n_, e_ = BBOX
    folium.Rectangle(
        bounds=[[s_, w_], [n_, e_]], color="#888", weight=1, fill=False, dash_array="6"
    ).add_to(m)
    folium.Marker(
        [lat, lon],
        popup="Your searched location",
        tooltip="Searched location",
        icon=folium.Icon(color="green", icon="star"),
    ).add_to(m)
    folium.Circle([lat, lon], radius=radius, color="#2a7", fill=True, fill_opacity=0.05).add_to(m)

    color_map = {"PG / Hostel": "blue", "Flat / Apartment": "purple", "Guest house": "cadetblue"}
    for s in stays:
        popup = (
            f"<b>{esc(s['Name'])}</b><br>{esc(s['Type'])}<br>"
            f"{esc(s['Address'])}<br>Phone: {esc(s['Phone'])}<br>"
            f"{s['Distance (m)']} m from search<br>"
            f"Nearest metro: {esc(s['Nearest Metro'])}"
        )
        folium.Marker(
            [s["lat"], s["lon"]],
            popup=folium.Popup(popup, max_width=280),
            tooltip=s["Name"],
            icon=folium.Icon(color=color_map.get(s["Type"], "blue"), icon="home"),
        ).add_to(m)

    for st_ in stations:
        folium.Marker(
            [st_["lat"], st_["lon"]],
            popup=f"<b>Metro: {esc(st_['Station'])}</b><br>{st_['Distance (m)']} m away",
            tooltip=f"🚇 {st_['Station']}",
            icon=folium.Icon(color="red", icon="train", prefix="fa"),
        ).add_to(m)
    for g in gates:
        folium.Marker(
            [g["lat"], g["lon"]],
            popup=f"<b>{esc(g['Gate'])}</b><br>{esc(g['Name'])}<br>{g['Distance (m)']} m away",
            tooltip=g["Gate"],
            icon=folium.DivIcon(
                html=(
                    "<div style='background:#ff9800;color:#fff;border-radius:50%;"
                    "width:24px;height:24px;line-height:24px;text-align:center;"
                    f"font-weight:bold;font-size:11px;border:2px solid #fff;'>{esc(g['Gate No.'] if g['Gate No.'] != '-' else 'G')}</div>"
                )
            ),
        ).add_to(m)

    st_folium(m, height=560, use_container_width=True, returned_objects=[])
    st.markdown(
        '<div class="legend"><span>🟢 Searched place</span><span>🔵 PG / Hostel</span>'
        '<span>🟣 Flat / Apartment</span><span>🔴 Metro station</span>'
        '<span>🟠 Numbered circle = metro gate</span></div>',
        unsafe_allow_html=True,
    )

    tab1, tab2, tab3, tab4 = st.tabs(["🏘️ PGs & Flats", "🚇 Metro stations", "🚪 Metro gates", "🔗 Live listings"])

    with tab1:
        if stays:
            df = pd.DataFrame(stays).drop(columns=["lat", "lon"])
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.download_button(
                "Download as CSV", df.to_csv(index=False).encode("utf-8"), "nearby_stays.csv", "text/csv"
            )
        else:
            st.info(
                "No PG/flat entries found in OpenStreetMap here. Try a bigger radius, "
                "or use the 'Live listings' tab."
            )
        st.info(
            "ℹ️ OpenStreetMap lists places, not vacancies or prices. Use the 'Live listings' tab "
            "to check current availability and rent for your chosen duration."
        )

    with tab2:
        if stations:
            st.dataframe(pd.DataFrame(stations).drop(columns=["lat", "lon"]), use_container_width=True, hide_index=True)
        else:
            st.info("No metro stations found within the radius.")

    with tab3:
        if gates:
            st.dataframe(pd.DataFrame(gates).drop(columns=["lat", "lon"]), use_container_width=True, hide_index=True)
            st.caption("Gate numbers come from OpenStreetMap tags; some entrances may not have a number tagged yet.")
        else:
            st.info("No metro gates/entrances found within the radius.")

    with tab4:
        st.write(f"Search current listings for **{duration} months** near **{place or chosen['name']}**:")
        area = place.strip() or chosen["name"].split(",")[0]
        for kind_word in ("PG", "Flat"):
            st.markdown(f"**{kind_word}**")
            links = search_links(area, kind_word, duration)
            cols = st.columns(len(links))
            for col, (label, url) in zip(cols, links.items()):
                col.link_button(label, url)

else:
    m = folium.Map(location=list(DEFAULT_CENTER), zoom_start=12, tiles="CartoDB positron")
    s_, w_, n_, e_ = BBOX
    folium.Rectangle(bounds=[[s_, w_], [n_, e_]], color="#888", weight=1, fill=False, dash_array="6").add_to(m)
    st_folium(m, height=520, use_container_width=True, returned_objects=[])
    st.info("👈 Enter a location in the sidebar and press Search.")

st.markdown(
    f"""
    <div class="footer">
      Original idea, design &amp; development by <b>{esc(DEVELOPER_NAME)}</b><br>
      <small>Map data © OpenStreetMap contributors</small>
    </div>
    """,
    unsafe_allow_html=True,
)
