"""
app.py

FindBack AI - Streamlit entry point.

This file renders UI only: it reads/writes `st.session_state`, calls
`services/*` for business logic, and `database/queries.py` for any data it
needs to display. It never runs raw SQL or calls AI providers directly.
"""

from __future__ import annotations

import os

import streamlit as st

from auth import get_current_user, has_permission, is_authenticated, logout, render_login_screen
from components.theme import (
    card_close,
    card_open,
    inject_css,
    notification_card_open,
    render_match_badge,
    render_notification_title,
    render_provider_status,
    render_score_bar,
    render_sidebar_brand,
)
from database.database import init_db
from database.queries import (
    get_claim_by_pair,
    get_item,
    get_item_features,
    get_items_by_type,
    get_notifications,
    get_recent_items,
    get_stats,
    get_unread_notification_count,
    get_user_by_id,
    list_claims,
    mark_all_notifications_read,
    mark_notification_read,
    reset_application_data,
)
from services.claim_service import approve_claim, create_claim_for_match, resolve_claim_handover
from services.item_service import create_item
from services.matching_service import find_matches_for_lost
from services.provider_service import ProviderManager
from utils.security import mask_key, validate_endpoint

st.set_page_config(page_title="FindBack AI", page_icon="🔍", layout="wide")
init_db()
inject_css()

_DEFAULTS = {
    "provider": "Groq",
    "auth_mode": "app",
    "model": "openai/gpt-oss-20b",
    "base_url": "https://api.groq.com/openai/v1",
    "enable_fallback": True,
    "user_api_key": "",
    "nav": "Home",
    "selected_match": None,
    "match_results": [],
    "match_lost_item": None,
}
for key, default in _DEFAULTS.items():
    st.session_state.setdefault(key, default)

if not is_authenticated():
    render_login_screen()
    st.stop()

current_user = get_current_user()
provider_manager = ProviderManager()

# Navigation permission matrix.
_NAV_ITEMS = [
    ("Home", "🏠  Home", "user"),
    ("Report Lost", "📤  Report Lost", "user"),
    ("Report Found", "📥  Report Found", "user"),
    ("My Reports", "📋  My Reports", "user"),
    ("Find Matches", "🔎  Find Matches", "user"),
    ("Notifications", "🔔  Notifications", "user"),
    ("Browse Items", "🗂️  Browse Items", "user"),
    ("Potential Claims", "📌  Potential Claims", "staff"),
    ("User Management", "👥  User Management", "staff"),
    ("Settings", "⚙️  Settings", "staff"),
    ("About", "ℹ️  About", "user"),
]
_nav_labels = {pid: label for pid, label, _ in _NAV_ITEMS}
_nav_access = {pid: level for pid, _, level in _NAV_ITEMS}
allowed_pages = [pid for pid, _, level in _NAV_ITEMS if (level == "user") or (level == "staff" and has_permission("staff")) or (level == "admin" and has_permission("admin"))]
page = st.session_state.get("nav", "Home")
if page not in allowed_pages:
    page = "Home"
    st.session_state["nav"] = page


def _item_reporter(item: dict | None) -> str:
    if not item:
        return "Unknown user"
    user = get_user_by_id(item.get("user_id")) if item.get("user_id") is not None else None
    if not user:
        return "Unknown user"
    return f"{user.get('full_name') or user.get('username')} ({user.get('username')})"


render_sidebar_brand()

st.sidebar.caption(f"Signed in as {current_user['full_name']} ({current_user['role']})")
unread_count = get_unread_notification_count(current_user["id"])
for pid, label, level in _NAV_ITEMS:
    if level == "admin" and not has_permission("admin"):
        continue
    if level == "staff" and not has_permission("staff"):
        continue
    if pid == "Notifications" and unread_count:
        label = f"🔔  Notifications ({unread_count})"
    selected = page == pid
    if st.sidebar.button(label, key=f"nav_{pid}", type="primary" if selected else "secondary", use_container_width=True):
        st.session_state["nav"] = pid
        st.rerun()

st.sidebar.divider()
provider_manager = ProviderManager()
render_provider_status(provider_manager.has_active_key(), st.session_state["provider"])
if st.sidebar.button("Logout", use_container_width=True):
    logout()
    st.rerun()

# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------
if page == "Home":
    st.title("Lost something? Let AI find it.")
    st.caption(f"Welcome back, {current_user['full_name']} · {current_user['role']}")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("📤 Report Lost Item", use_container_width=True):
            st.session_state["nav"] = "Report Lost"
            st.rerun()
    with c2:
        if st.button("📥 Report Found Item", use_container_width=True):
            st.session_state["nav"] = "Report Found"
            st.rerun()
    if st.button("🔎 Find Matches", use_container_width=True, type="primary"):
        st.session_state["nav"] = "Find Matches"
        st.rerun()

    stats = get_stats()
    st.divider()
    cols = st.columns(4)
    cols[0].metric("Total Reports", stats["total"])
    cols[1].metric("Lost Items", stats["lost"])
    cols[2].metric("Found Items", stats["found"])
    cols[3].metric("Potential Claims", stats["matches"])

elif page in ["Report Lost", "Report Found"]:
    is_lost = page == "Report Lost"
    st.title(page)
    with st.form("report"):
        title = st.text_input("Item Name*")
        desc = st.text_area("Description*")
        cat = st.selectbox("Category", ["laptop_bag", "backpack", "phone", "wallet", "keys", "other"])
        brand = st.text_input("Brand")
        color = st.text_input("Color")
        features = st.text_input("Distinctive Features")
        loc = st.selectbox(
            "Location",
            ["Computer Science Department", "Library", "Cafeteria", "Main Gate", "Hostel",
             "Parking Area", "Administration Block", "Laboratory", "Sports Ground", "Other"],
        )
        date = st.date_input("Date")
        time_ = st.time_input("Time")
        img = st.file_uploader("Image (JPG/PNG/WEBP, max 5MB)", type=["jpg", "jpeg", "png", "webp"])
        st.text_input("Contact Preference (optional)")
        submitted = st.form_submit_button("Submit Report")

    if submitted:
        if not title or not desc:
            st.error("Title and description are required.")
            st.stop()
        with st.status("Analyzing item...", expanded=True) as status:
            st.write("✓ Reading description")
            st.write("✓ Extracting characteristics")
            st.write("✓ Generating semantic representation")
            try:
                result = create_item(
                    "lost" if is_lost else "found",
                    title,
                    desc + " " + features,
                    cat,
                    brand,
                    color,
                    loc,
                    str(date),
                    time_.strftime("%H:%M"),
                    img,
                    user_id=current_user["id"],
                )
                status.update(label="Match analysis complete", state="complete")
            except Exception as exc:
                status.update(label="Something went wrong", state="error")
                st.error(f"Unable to analyze: {exc}. Try switching provider or adding your own API key in Settings.")
                st.stop()

        st.toast(f"Report #{result.item_id} created!", icon="✅")
        st.success(f"Report #{result.item_id} created! AI-extracted features saved.")
        for warning in result.warnings:
            st.warning(warning)

elif page == "Find Matches":
    st.title("Find Matches")
    lost_items = get_items_by_type("lost", status="active")
    if not lost_items:
        st.info("No active lost reports yet.")
        st.stop()

    sel = st.selectbox(
        "Select your lost item",
        lost_items,
        format_func=lambda x: f"#{x['id']} {x['title']} - {x['location']}",
    )
    if st.button("Search Matches", type="primary"):
        st.session_state["selected_match"] = None
        with st.spinner("Scoring candidates..."):
            st.session_state["match_results"] = find_matches_for_lost(sel["id"], top_k=5)
        st.session_state["match_lost_item"] = sel

    results = st.session_state.get("match_results", [])
    lost_for_results = st.session_state.get("match_lost_item")

    if results and lost_for_results and lost_for_results["id"] == sel["id"]:
        for r in results:
            found = r["found"]
            scores = r["scores"]
            card_open()
            c1, c2 = st.columns([1, 2])
            with c1:
                if found["image_path"] and os.path.exists(found["image_path"]):
                    st.image(found["image_path"], width=200)
                else:
                    st.caption("No photo provided")
            with c2:
                render_match_badge(r["final"], r["label"])
                st.markdown(f"**{found['title']}** found near {found['location']}")
                st.caption(f"Reported by: {_item_reporter(found)}")
                st.caption(f"Lost item reported by: {_item_reporter(sel)}")
                render_score_bar("Text Similarity", scores["text"])
                render_score_bar("Image Similarity", scores["image"])
                render_score_bar("Location", scores["location"])
                render_score_bar("Category", scores["category"])
                render_score_bar("Time", scores["time"])
                if st.button("View Details", key=f"view_{found['id']}"):
                    st.session_state["selected_match"] = r
            card_close()

        match = st.session_state.get("selected_match")
        if match:
            found = match["found"]
            st.divider()
            st.subheader("AI Match Analysis")
            render_match_badge(match["final"], match["label"])
            st.caption(f"Lost item: #{sel['id']} '{sel['title']}' — reported by {_item_reporter(sel)}")
            st.caption(f"Found item: #{found['id']} '{found['title']}' — reported by {_item_reporter(found)}")
            prov = provider_manager.get_provider()
            if prov:
                try:
                    explanation = prov.generate_explanation({"scores": match["scores"], "lost": dict(sel), "found": dict(found)})
                    st.info(explanation)
                except Exception as exc:
                    st.warning(f"Explanation unavailable: {exc}")
            else:
                st.caption("Connect an AI provider in Settings to get a natural-language explanation.")

            st.write("Why AI thinks they match:")
            if match["scores"]["category"] > 0.8:
                st.write("✓ Same item category")
            if match["scores"]["brand_color"] > 0.4:
                st.write("✓ Same color/brand")
            if match["scores"]["location"] > 0.8:
                st.write("✓ Same location")
            if match["scores"]["time"] > 0.7:
                st.write("✓ Found shortly after lost")

            if st.button("✅ This is My Item", use_container_width=True):
                claim_id = create_claim_for_match(sel["id"], found["id"], current_user["id"], notes=f"Claim created by {current_user['username']}")
                if claim_id:
                    st.session_state["selected_match"] = None
                    st.toast("Claim created and submitted for review.", icon="🎉")
                    st.success("Claim created and submitted for review.")
                else:
                    st.warning("This item pair is already claimed or resolved.")

elif page == "My Reports":
    st.title("My Reports")
    if current_user["role"] == "Regular User":
        rows = get_recent_items(limit=50)
        rows = [r for r in rows if r.get("user_id") == current_user["id"]]
    else:
        rows = get_recent_items(limit=50)
    if not rows:
        st.info("No reports yet.")
    else:
        for r in rows:
            card_open()
            st.write(f"**{r['type'].upper()}** #{r['id']} {r['title']} - {r['location']} - {r['status']}")
            if r["image_path"] and os.path.exists(r["image_path"]):
                st.image(r["image_path"], width=160)
            desc = r["description"] or ""
            st.caption(desc[:220] + ("..." if len(desc) > 220 else ""))
            card_close()

elif page == "Notifications":
    st.title("🔔 Notifications")
    notifs = get_notifications(current_user["id"], limit=50)
    unread = [n for n in notifs if not n["is_read"]]

    col_a, col_b = st.columns([3, 1])
    with col_a:
        st.caption(f"{len(unread)} unread · {len(notifs)} total")
    with col_b:
        if unread and st.button("Mark all as read", use_container_width=True):
            mark_all_notifications_read(current_user["id"])
            st.rerun()

    if not notifs:
        st.info(
            "No notifications yet. When staff confirms that your lost item has been found "
            "(or that an item you found has an owner), you'll see it here."
        )
    else:
        for n in notifs:
            is_unread = not n["is_read"]
            notification_card_open(is_unread)
            render_notification_title(n["title"], is_unread)
            st.write(n["message"])
            st.caption(n["created_at"])
            if is_unread:
                if st.button("Mark as read", key=f"read_{n['id']}"):
                    mark_notification_read(n["id"])
                    st.rerun()
            card_close()

elif page == "Potential Claims":
    st.title("Potential Claims / Claim History")
    claims = list_claims()
    if current_user["role"] == "Regular User":
        claims = [c for c in claims if c.get("requested_by_user_id") == current_user["id"]]
    if not claims:
        st.info("No claims recorded yet.")
    else:
        for claim in claims:
            lost = get_item(claim["lost_item_id"])
            found = get_item(claim["found_item_id"])
            if not lost or not found:
                continue
            status_label = {
                "potential": "Potential Claim",
                "approved": "Claim Approved",
                "resolved": "Resolved / Closed",
            }.get(claim.get("status"), claim.get("status"))
            st.markdown(f"### {status_label} · #{claim['id']}")
            st.write(f"Lost Item: #{lost['id']} {lost['title']} ({lost['status']}) — reported by {_item_reporter(lost)}")
            st.write(f"Found Item: #{found['id']} {found['title']} ({found['status']}) — reported by {_item_reporter(found)}")
            st.write(f"Verification: {claim.get('verification_status', 'pending')} | Handover: {claim.get('handover_status', 'pending')}")
            if claim.get("notes"):
                st.caption(claim["notes"])
            if claim["status"] == "potential" and has_permission("staff"):
                if st.button(f"Approve claim #{claim['id']}", key=f"approve_{claim['id']}"):
                    approve_claim(claim["id"], current_user["id"], notes="Approved by staff review")
                    st.success("Claim approved.")
                    st.rerun()
            if claim["status"] == "approved" and has_permission("staff"):
                if st.button(f"Confirm handover #{claim['id']}", key=f"handover_{claim['id']}"):
                    resolve_claim_handover(claim["id"], current_user["id"], notes="Handover completed and recorded")
                    st.success("Claim resolved and items closed.")
                    st.rerun()
            if claim["status"] == "resolved":
                st.info("Handed Over and Resolved")
            st.divider()

elif page == "User Management":
    if not has_permission("staff"):
        st.error("Access denied. Staff or Administrator access required.")
        st.stop()
    st.title("User Management")
    st.divider()
    st.subheader("⚠️ Reset FindBack Data")

    st.warning(
        "This will permanently delete all lost/found items, matches, claims, "
        "notifications, AI usage records, and item features. User accounts will NOT be deleted."
    )

    reset_confirmation = st.text_input(
        "Type RESET to enable the reset button:",
        key="reset_confirmation"
    )

    if st.button(
        "🗑️ Reset All FindBack Data",
        type="secondary",
        disabled=(reset_confirmation != "RESET")
    ):
        try:
            reset_application_data()
            st.success("✅ All FindBack data has been reset successfully.")
            st.rerun()
        except Exception as e:
            st.error(f"Reset failed: {e}")
    from database.queries import get_all_users
    users = get_all_users()
    for user in users:
        st.markdown(
            f"""
            <div class="fb-card">
                <div><strong>{user.get('full_name') or user.get('username')}</strong></div>
                <div style="margin-top: 0.35rem; color: #475569;">
                    <div>Username: {user.get('username', '—')}</div>
                    <div>Email: {user.get('email') or '—'}</div>
                    <div>Phone: {user.get('phone_number') or '—'}</div>
                    <div>Role: {user.get('role', '—')}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.caption("Demo credentials: admin/Admin@123, staff/Staff@123, user1/User@123, user2/User@123, user3/User@123")

elif page == "Browse Items":
    st.title("Browse Items")
    rows = get_recent_items(limit=50)
    for r in rows:
        card_open()
        st.write(f"**{r['type'].upper()}** #{r['id']} {r['title']} - {r['location']} - {r['event_date']}")
        if r["image_path"] and os.path.exists(r["image_path"]):
            st.image(r["image_path"], width=150)
        card_close()

elif page == "Settings":
    if not has_permission("staff"):
        st.error("Access denied. Staff or Administrator access required.")
        st.stop()
    st.title("Settings - AI Providers")
    prov_name = st.selectbox("AI Provider", ["Groq", "HuggingFace"], index=0 if st.session_state["provider"] == "Groq" else 1)
    auth = st.radio("Authentication", ["Application Default", "My API Key"], index=0 if st.session_state["auth_mode"] == "app" else 1)
    api_key_input = st.text_input("API Key", type="password", placeholder="••••••••")
    model = st.text_input("Model", value=st.session_state["model"])
    base_url = st.text_input("Endpoint", value=st.session_state["base_url"])
    fallback = st.checkbox("Enable automatic fallback", value=st.session_state["enable_fallback"])

    col_test, col_save, col_remove = st.columns(3)

    if col_test.button("Test Connection"):
        if not validate_endpoint(base_url):
            st.error("Endpoint not in allowlist. Allowed: https://api.groq.com/ , https://router.huggingface.co/")
        else:
            from ai.groq_provider import GroqProvider
            from ai.huggingface_provider import HuggingFaceProvider

            if auth == "My API Key":
                key = api_key_input
            else:
                key = provider_manager.resolve_app_key(prov_name)
            if not key:
                st.warning("No API key is configured for this provider. Add one above with 'My API Key' or provide a secret in .streamlit/secrets.toml.")
                st.stop()
            provider_cls = GroqProvider if prov_name == "Groq" else HuggingFaceProvider
            with st.spinner("Testing connection..."):
                ok, msg = provider_cls(key, model, base_url).test_connection()
            if ok:
                st.success(msg)
            else:
                st.error(msg)

    if col_save.button("Save for This Session"):
        st.session_state["provider"] = prov_name
        st.session_state["auth_mode"] = "user" if auth == "My API Key" else "app"
        st.session_state["model"] = model
        st.session_state["base_url"] = base_url
        st.session_state["enable_fallback"] = fallback
        if api_key_input:
            st.session_state["user_api_key"] = api_key_input
        st.toast("Settings saved for this session", icon="⚙️")
        st.success(f"Saved. Key: {mask_key(api_key_input) if api_key_input else 'App Default'} - Session only")

    if col_remove.button("Remove Key"):
        st.session_state["user_api_key"] = ""
        st.toast("User key removed", icon="🗑️")
        st.success("User key removed from session")

    st.divider()
    st.caption("Security: user keys are stored in st.session_state only. They are never logged, never committed, and are masked in the UI. The server must still receive the key to call the provider.")

elif page == "About":
    st.title("About FindBack AI")
    st.write("FindBack AI does not search for identical words. It searches for the same meaning and combines multiple signals: semantic understanding, image understanding, location, time, and distinctive features.")
    st.write("Traditional: Keyword Search → FindBack: Semantic + Visual + Context + AI Explanation")
