from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import extra_streamlit_components as stx
import pandas as pd
import streamlit as st

from draftboard.data.db import (
    arm_autopick,
    claim_team_gateway_link,
    disable_autopick,
    ensure_team_gateway_links,
    finalize_lottery,
    get_autopick_open_picks,
    get_autopick_state,
    get_dashboard_summary,
    get_draft_clock_config,
    get_draft_clock_snapshot,
    get_draft_initialization_readiness,
    get_live_draft_board,
    get_live_draft_state,
    get_lottery_state,
    get_player_universe,
    get_team_gateway_links,
    get_team_gateway_audit,
    get_teams,
    refresh_yahoo_teams_live,
    initialize_lottery,
    reveal_next_lottery_slot,
    initialize_draft_from_lottery,
    pause_nfhl_draft,
    resume_nfhl_draft,
    save_autopick_queue,
    save_draft_clock_config,
    start_nfhl_draft,
    submit_manual_draft_pick,
    void_lottery,
    write_team_gateway_audit,
    apply_nfhl_standard_clock_config,
    is_nfhl_standard_clock_configured,
    set_nfhl_current_pick_remaining,
)
from draftboard.state.runtime import (
    get_league_key,
    get_season_year,
)


from draftboard.ui.components.draft_lottery import (
    render_nfhl_lottery_board,
)
CONFIG_PATH = Path(
    f"/league_runtime/config/nfhl_{get_season_year()}.json"
)

BLUE = "#002868"
LIGHT_BLUE = "#4B92DB"
NAVY = "#001B3F"



NFHL_GATEWAY_COOKIE_NAME = "nfhl_team_gateway"

NFHL_GATEWAY_SECRET_PATH = Path(
    "/league_runtime/config/.nfhl_gateway_cookie_secret"
)


# NFHL_TEAM_GATEWAY_UI_START
# ================================================================
# NFHL TEAM GATEWAY
#
# Commissioner:
#   /?commissioner=1
#
# Manager:
#   /?team=<private token>
#
# Manager identity is remembered in a signed browser cookie.
# ================================================================


def _nfhl_gateway_secret() -> str:
    env_secret = str(
        os.environ.get(
            "NFHL_GATEWAY_COOKIE_SECRET",
            "",
        )
        or ""
    ).strip()

    if env_secret:
        return env_secret

    if NFHL_GATEWAY_SECRET_PATH.exists():
        secret = (
            NFHL_GATEWAY_SECRET_PATH
            .read_text(
                encoding="utf-8",
            )
            .strip()
        )

        if secret:
            return secret

    raise RuntimeError(
        "NFHL Team Gateway signing secret is unavailable."
    )


def _nfhl_gateway_sign(
    payload_b64: str,
) -> str:
    return hmac.new(
        _nfhl_gateway_secret().encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _nfhl_gateway_pack(
    payload: dict,
) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    payload_b64 = (
        base64.urlsafe_b64encode(raw)
        .decode("ascii")
        .rstrip("=")
    )

    signature = _nfhl_gateway_sign(
        payload_b64
    )

    return (
        f"{payload_b64}.{signature}"
    )


def _nfhl_gateway_unpack(
    token: str | None,
) -> dict | None:
    if not token:
        return None

    token_text = str(token)

    if "." not in token_text:
        return None

    payload_b64, signature = (
        token_text.split(".", 1)
    )

    expected = _nfhl_gateway_sign(
        payload_b64
    )

    if not hmac.compare_digest(
        signature,
        expected,
    ):
        return None

    try:
        padded = (
            payload_b64
            + "=" * (
                -len(payload_b64) % 4
            )
        )

        payload = json.loads(
            base64.urlsafe_b64decode(
                padded.encode("ascii")
            ).decode("utf-8")
        )

    except Exception:
        return None

    role = str(
        payload.get("role")
        or ""
    ).strip().lower()

    if role not in {
        "commissioner",
        "manager",
    }:
        return None

    # Prevent a remembered manager identity from silently
    # carrying across another NFHL league/season.
    if str(
        payload.get("league_key")
        or ""
    ) != str(get_league_key()):
        return None

    try:
        payload_season = int(
            payload.get("season_year")
        )
    except Exception:
        return None

    if payload_season != int(
        get_season_year()
    ):
        return None

    return payload


def _nfhl_gateway_context(
    payload: dict | None,
) -> dict[str, object]:
    if not payload:
        return {
            "role": "public",
            "team_key": None,
            "team_name": None,
            "display_name": "Public",
            "acting_as": "public",
        }

    role = str(
        payload.get("role")
        or ""
    ).strip().lower()

    return {
        "role": role,
        "team_key": payload.get(
            "team_key"
        ),
        "team_name": payload.get(
            "team_name"
        ),
        "display_name": (
            payload.get("display_name")
            or payload.get("team_name")
            or role
        ),
        "acting_as": (
            payload.get("acting_as")
            or role
        ),
    }


def _gateway_value(
    payload: dict | None,
    key: str,
) -> str | None:
    if not payload:
        return None

    value = str(
        payload.get(key)
        or ""
    ).strip()

    return value or None


def render_team_gateway() -> dict[str, object]:
    gateway_notice = st.session_state.pop(
        "nfhl_gateway_notice",
        None,
    )

    if gateway_notice:
        st.success(
            str(gateway_notice)
        )

    commissioner_url = (
        str(
            st.query_params.get(
                "commissioner",
                "0",
            )
        )
        == "1"
    )

    cookie_manager = stx.CookieManager(
        key="nfhl_team_gateway_cookie_manager"
    )

    cookies = (
        cookie_manager.get_all()
        or {}
    )

    # ------------------------------------------------------------
    # COMMISSIONER URL
    # ------------------------------------------------------------

    if commissioner_url:
        payload = {
            "role": "commissioner",
            "league_key": (
                get_league_key()
            ),
            "season_year": (
                get_season_year()
            ),
            "team_key": None,
            "team_name": (
                "Commissioner"
            ),
            "display_name": (
                "Commissioner"
            ),
            "acting_as": (
                "commissioner"
            ),
            "created_at_utc": (
                datetime.utcnow()
                .isoformat(
                    timespec="seconds"
                )
            ),
        }

        ctx = _nfhl_gateway_context(
            payload
        )

        st.session_state[
            "nfhl_gateway_context"
        ] = ctx

        return ctx

    # ------------------------------------------------------------
    # PRIVATE MANAGER LINK
    # ------------------------------------------------------------

    link_token = str(
        st.query_params.get("team")
        or st.query_params.get(
            "team_token"
        )
        or ""
    ).strip()

    previous_payload = (
        _nfhl_gateway_unpack(
            cookies.get(
                NFHL_GATEWAY_COOKIE_NAME
            )
        )
    )

    if link_token:
        linked_team = (
            claim_team_gateway_link(
                link_token
            )
        )

        if not linked_team:
            st.error(
                "This NFHL team link is invalid or inactive. "
                "Ask the Commissioner for a fresh link."
            )

            ctx = (
                _nfhl_gateway_context(
                    None
                )
            )

            st.session_state[
                "nfhl_gateway_context"
            ] = ctx

            return ctx

        payload = {
            "role": "manager",
            "league_key": str(
                linked_team[
                    "league_key"
                ]
            ),
            "season_year": int(
                linked_team[
                    "season_year"
                ]
            ),
            "team_key": str(
                linked_team[
                    "team_key"
                ]
            ),
            "team_name": str(
                linked_team[
                    "team_name"
                ]
            ),
            "display_name": str(
                linked_team[
                    "owner_name"
                ]
                or linked_team[
                    "team_name"
                ]
            ),
            "acting_as": (
                "manager:"
                + str(
                    linked_team[
                        "team_name"
                    ]
                )
            ),
            "created_at_utc": (
                datetime.utcnow()
                .isoformat(
                    timespec="seconds"
                )
            ),
        }

        try:
            write_team_gateway_audit(
                selected_role=(
                    "manager"
                ),
                selected_team_key=(
                    str(
                        payload[
                            "team_key"
                        ]
                    )
                ),
                selected_team_name=(
                    str(
                        payload[
                            "team_name"
                        ]
                    )
                ),
                previous_role=(
                    _gateway_value(
                        previous_payload,
                        "role",
                    )
                ),
                previous_team_key=(
                    _gateway_value(
                        previous_payload,
                        "team_key",
                    )
                ),
                previous_team_name=(
                    _gateway_value(
                        previous_payload,
                        "team_name",
                    )
                ),
                action_type=(
                    "CLAIM_TEAM_LINK"
                ),
                action_note=(
                    "Browser gateway identity "
                    "set from NFHL manager link."
                ),
                query_string=str(
                    dict(
                        st.query_params
                    )
                ),
            )

        except Exception as exc:
            st.warning(
                "Team Gateway audit logging failed: "
                f"{exc}"
            )

        signed_cookie = (
            _nfhl_gateway_pack(
                payload
            )
        )

        cookie_manager.set(
            NFHL_GATEWAY_COOKIE_NAME,
            signed_cookie,
            expires_at=(
                datetime.utcnow()
                + timedelta(
                    days=180
                )
            ),
        )

        ctx = (
            _nfhl_gateway_context(
                payload
            )
        )

        st.session_state[
            "nfhl_gateway_context"
        ] = ctx

        # Carry the confirmation through the URL-cleanup rerun.
        st.session_state[
            "nfhl_gateway_notice"
        ] = (
            "Remembered this browser as "
            f"{ctx.get('team_name')}."
        )

        # Same NFFL behavior: once the private URL is
        # claimed, remove the token from the address bar.
        try:
            st.query_params.clear()
        except Exception:
            pass

        return ctx

    # ------------------------------------------------------------
    # RETURN VISIT — RESTORE MANAGER FROM COOKIE
    # ------------------------------------------------------------

    cookie_payload = (
        _nfhl_gateway_unpack(
            cookies.get(
                NFHL_GATEWAY_COOKIE_NAME
            )
        )
    )

    # A commissioner identity is never accepted from a cookie
    # on the ordinary public URL.
    if (
        cookie_payload
        and cookie_payload.get(
            "role"
        )
        == "commissioner"
    ):
        cookie_manager.delete(
            NFHL_GATEWAY_COOKIE_NAME
        )

        cookie_payload = None

    if cookie_payload:
        ctx = (
            _nfhl_gateway_context(
                cookie_payload
            )
        )

        st.session_state[
            "nfhl_gateway_context"
        ] = ctx

        with st.sidebar:
            with st.expander(
                "Team Gateway",
                expanded=False,
            ):
                st.caption(
                    "This browser is remembered as:"
                )

                st.write(
                    f"**{ctx.get('team_name')}**"
                )

                if st.button(
                    "Clear This Browser",
                    key=(
                        "nfhl_gateway_clear_main"
                    ),
                ):
                    try:
                        write_team_gateway_audit(
                            selected_role=(
                                "public"
                            ),
                            selected_team_key=None,
                            selected_team_name=None,
                            previous_role=(
                                _gateway_value(
                                    ctx,
                                    "role",
                                )
                            ),
                            previous_team_key=(
                                _gateway_value(
                                    ctx,
                                    "team_key",
                                )
                            ),
                            previous_team_name=(
                                _gateway_value(
                                    ctx,
                                    "team_name",
                                )
                            ),
                            action_type=(
                                "CLEAR_BROWSER"
                            ),
                            action_note=(
                                "NFHL browser gateway "
                                "identity cleared."
                            ),
                            query_string=str(
                                dict(
                                    st.query_params
                                )
                            ),
                        )

                    except Exception as exc:
                        st.warning(
                            "Team Gateway audit logging failed: "
                            f"{exc}"
                        )

                    cookie_manager.delete(
                        NFHL_GATEWAY_COOKIE_NAME
                    )

                    st.session_state.pop(
                        "nfhl_gateway_context",
                        None,
                    )

                    st.rerun()

        return ctx

    ctx = (
        _nfhl_gateway_context(
            None
        )
    )

    st.session_state[
        "nfhl_gateway_context"
    ] = ctx

    return ctx


def render_manager_links() -> None:
    st.subheader(
        "Manager Team Links"
    )

    st.caption(
        "Give each manager only their own link. "
        "Opening it remembers that browser as "
        "the correct NFHL team."
    )

    created = (
        ensure_team_gateway_links()
    )

    if created:
        st.success(
            f"Created {created} missing manager "
            f"link{'s' if created != 1 else ''}."
        )

    rows = (
        get_team_gateway_links()
    )

    if not rows:
        st.info(
            "No NFHL teams are available."
        )
        return

    base_url = str(
        os.environ.get(
            "NFHL_PUBLIC_URL",
            "https://nfhl.majorleaguefantasy.app",
        )
    ).rstrip("/")

    display_rows = []

    for row in rows:
        token = str(
            row.get(
                "link_token"
            )
            or ""
        )

        claimed = row.get(
            "last_claimed_at_utc"
        )

        if claimed:
            claimed_text = (
                pd.to_datetime(
                    claimed,
                    utc=True,
                )
                .tz_convert(
                    "America/New_York"
                )
                .strftime(
                    "%Y-%m-%d %-I:%M:%S %p %Z"
                )
            )
        else:
            claimed_text = ""

        display_rows.append(
            {
                "Team": (
                    row.get(
                        "team_name"
                    )
                    or ""
                ),
                "Manager": (
                    row.get(
                        "owner_name"
                    )
                    or ""
                ),
                "Active": bool(
                    row.get(
                        "is_active"
                    )
                ),
                "Claims": int(
                    row.get(
                        "claim_count"
                    )
                    or 0
                ),
                "Last Claimed Eastern": (
                    claimed_text
                ),
                "Manager Link": (
                    f"{base_url}/?team={token}"
                    if token
                    else ""
                ),
            }
        )

    import html as html_lib
    import streamlit.components.v1 as components

    def _esc(
        value: object,
    ) -> str:
        return html_lib.escape(
            ""
            if value is None
            else str(value),
            quote=True,
        )

    html_rows = []

    for link_row in display_rows:
        manager_url = str(
            link_row.get(
                "Manager Link"
            )
            or ""
        )

        html_rows.append(
            "<tr>"
            f"<td>{_esc(link_row.get('Team'))}</td>"
            f"<td>{_esc(link_row.get('Manager'))}</td>"
            f"<td>{_esc(link_row.get('Active'))}</td>"
            f"<td>{_esc(link_row.get('Claims'))}</td>"
            f"<td>{_esc(link_row.get('Last Claimed Eastern'))}</td>"
            "<td>"
            f"<button class='copy-btn' "
            f"type='button' "
            f"data-copy='{_esc(manager_url)}' "
            f"onclick='copyManagerLink(this)'>"
            "Copy"
            "</button>"
            "<span class='copy-status' "
            "aria-live='polite'></span>"
            "</td>"
            f"<td><code class='manager-link'>"
            f"{_esc(manager_url)}"
            "</code></td>"
            "</tr>"
        )

    html_doc = f"""
    <style>
      .manager-links-wrap {{
        font-family:
          system-ui,
          -apple-system,
          BlinkMacSystemFont,
          "Segoe UI",
          sans-serif;
        width: 100%;
        color: #f8fafc;
      }}

      table.manager-links-table {{
        border-collapse: collapse;
        width: 100%;
        table-layout: fixed;
        font-size: 14px;
        background: #0f172a;
        color: #f8fafc;
      }}

      .manager-links-table th,
      .manager-links-table td {{
        border:
          1px solid
          rgba(226, 232, 240, 0.24);
        padding: 8px 10px;
        vertical-align: top;
        text-align: left;
        color: #f8fafc;
      }}

      .manager-links-table th {{
        background: #1e293b;
        font-weight: 700;
        color: #f8fafc;
      }}

      .manager-links-table th:nth-child(3),
      .manager-links-table td:nth-child(3),
      .manager-links-table th:nth-child(4),
      .manager-links-table td:nth-child(4) {{
        width: 70px;
      }}

      .manager-links-table th:nth-child(6),
      .manager-links-table td:nth-child(6) {{
        width: 120px;
      }}

      .copy-btn {{
        cursor: pointer;
        border:
          1px solid
          rgba(226, 232, 240, 0.45);
        border-radius: 6px;
        padding: 5px 10px;
        background: #f8fafc;
        color: #0f172a;
        font-weight: 700;
      }}

      .copy-btn:hover {{
        background: #e2e8f0;
      }}

      .copy-status {{
        display: block;
        margin-top: 4px;
        font-size: 12px;
        color: #86efac;
        min-height: 16px;
        font-weight: 700;
      }}

      .manager-link {{
        white-space: normal;
        overflow-wrap: anywhere;
        word-break: break-word;
        color: #bae6fd;
        background:
          rgba(15, 23, 42, 0.75);
      }}
    </style>

    <div class="manager-links-wrap">
      <table class="manager-links-table">
        <thead>
          <tr>
            <th>Team</th>
            <th>Manager</th>
            <th>Active</th>
            <th>Claims</th>
            <th>Last Claimed Eastern</th>
            <th>Copy</th>
            <th>Manager Link</th>
          </tr>
        </thead>

        <tbody>
          {''.join(html_rows)}
        </tbody>
      </table>
    </div>

    <script>
      function copyManagerLink(button) {{
        const text =
          button.getAttribute(
            "data-copy"
          ) || "";

        const status =
          button.parentElement
            .querySelector(
              ".copy-status"
            );

        function markDone(message) {{
          const originalText =
            button.getAttribute(
              "data-original-text"
            )
            || button.textContent
            || "Copy";

          button.setAttribute(
            "data-original-text",
            originalText
          );

          button.textContent =
            message;

          if (status) {{
            status.textContent =
              message;
          }}

          window.setTimeout(
            () => {{
              button.textContent =
                originalText;

              if (status) {{
                status.textContent =
                  "";
              }}
            }},
            1800
          );
        }}

        function fallbackCopy() {{
          const ta =
            document.createElement(
              "textarea"
            );

          ta.value = text;

          ta.setAttribute(
            "readonly",
            ""
          );

          ta.style.position =
            "fixed";
          ta.style.left =
            "-9999px";
          ta.style.top =
            "0";

          document.body
            .appendChild(ta);

          ta.focus();
          ta.select();

          try {{
            const ok =
              document.execCommand(
                "copy"
              );

            markDone(
              ok
                ? "Copied"
                : "Copy failed"
            );

          }} catch (err) {{
            markDone(
              "Copy failed"
            );

          }} finally {{
            document.body
              .removeChild(ta);
          }}
        }}

        if (
          navigator.clipboard
          && window.isSecureContext
        ) {{
          navigator.clipboard
            .writeText(text)
            .then(
              () => markDone(
                "Copied"
              )
            )
            .catch(
              () => fallbackCopy()
            );

        }} else {{
          fallbackCopy();
        }}
      }}
    </script>
    """

    manager_links_height = (
        110
        + (
            len(display_rows)
            * 72
        )
    )

    components.html(
        html_doc,
        height=manager_links_height,
        scrolling=False,
    )

    st.caption(
        "Each Manager Link is a private credential "
        "for that team."
    )



def render_gateway_audit() -> None:
    """
    Commissioner-only read view of recent Team Gateway identity events.
    """

    st.markdown(
        "#### Gateway Audit"
    )

    st.caption(
        "Recent browser identity selections and changes. "
        "This history is read-only."
    )

    try:
        rows = get_team_gateway_audit(
            limit=200,
        )

    except Exception as exc:
        st.error(
            "Could not load Gateway Audit: "
            f"{exc}"
        )
        return

    if not rows:
        st.info(
            "No Team Gateway activity has been logged yet."
        )
        return

    import pandas as pd

    df = pd.DataFrame(
        rows
    )

    df["created_at_utc"] = (
        pd.to_datetime(
            df["created_at_utc"],
            utc=True,
        )
        .dt.tz_convert(
            "America/New_York"
        )
        .dt.strftime(
            "%Y-%m-%d %-I:%M:%S %p %Z"
        )
    )

    df = df.rename(
        columns={
            "created_at_utc": "Time Eastern",
            "action_type": "Action",
            "selected_role": "Selected Role",
            "selected_team_name": "Selected Team",
            "previous_role": "Previous Role",
            "previous_team_name": "Previous Team",
            "action_note": "Note",
        }
    )

    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
    )


# NFHL_TEAM_GATEWAY_UI_END


def load_config() -> dict:
    with CONFIG_PATH.open(
        "r",
        encoding="utf-8",
    ) as handle:
        return json.load(handle)


@st.cache_data(ttl=60)
def load_summary() -> dict:
    return get_dashboard_summary()


@st.cache_data(ttl=60)
def load_teams() -> list[dict]:
    return get_teams()


@st.cache_data(ttl=60)
def load_players() -> list[dict]:
    return get_player_universe()


@st.cache_data(ttl=5)
def load_lottery_state() -> dict | None:
    return get_lottery_state()


def apply_theme() -> None:
    st.markdown(
        f"""
        <style>
        .block-container {{
            max-width: 100% !important;

            /*
             * Match NFFL's reserved top space for the fixed
             * draft/player-picker control area.
             */
            padding-top: 5.15rem !important;
            padding-bottom: 3rem;

            padding-left: 0.25rem !important;
            padding-right: 0.25rem !important;
        }}

        /*
         * NFHL header intentionally mirrors the visual structure
         * of the proven NFFL DraftBoard header while retaining
         * independent hockey branding.
         */

                div[data-testid="stElementContainer"]:has(.nfhl-draftboard-hero-shell),
        div[data-testid="stMarkdownContainer"]:has(.nfhl-draftboard-hero-shell) {{
            width: 100% !important;
            max-width: none !important;
            box-sizing: border-box;
        }}

        .nfhl-draftboard-hero-shell {{
            width: 100%;
            max-width: none;
            margin: 0 0 16px 0;
            padding: 0;
            box-sizing: border-box;
        }}

                .nfhl-draftboard-hero {{
            display: flex;
            align-items: center;
            justify-content: flex-start;
            gap: 26px;

            width: 100%;
            min-height: 126px;

            background:
                linear-gradient(
                    135deg,
                    #001B3F 0%,
                    #002868 54%,
                    #003B80 100%
                );

            border: 3px solid #4B92DB;
            border-radius: 18px;

            padding: 24px 32px;
            margin: 0;

            box-sizing: border-box;

            box-shadow:
                0 0 20px rgba(0, 40, 104, 0.30);
        }}

        .nfhl-draftboard-logo {{
            width: 92px;
            height: 92px;

            border-radius: 14px;
            border: 3px solid {LIGHT_BLUE};

            display: flex;
            align-items: center;
            justify-content: center;

            font-weight: 950;
            font-size: 1.72rem;

            color: #FFFFFF;
            background: {NAVY};

            letter-spacing: 0.03em;

            flex: 0 0 auto;

            box-shadow:
                0 0 18px rgba(75, 146, 219, 0.28);
        }}

        .nfhl-draftboard-title-wrap {{
            line-height: 1.0;
            text-align: left;
        }}

        .nfhl-draftboard-kicker {{
            color: {LIGHT_BLUE};

            font-weight: 850;
            font-size: 1.00rem;

            letter-spacing: 0.20em;
            text-transform: uppercase;

            margin-bottom: 9px;
        }}

        .nfhl-draftboard-title {{
            color: #F7F7F7;

            font-weight: 950;
            font-size: clamp(2.8rem, 7vw, 5.6rem);

            letter-spacing: -0.05em;
            text-transform: uppercase;
        }}

        .nfhl-draftboard-title-year {{
            color: {LIGHT_BLUE};
        }}

        h1, h2, h3 {{
            color: {BLUE};
        }}

        div[data-testid="stMetric"] {{
            border-top: 4px solid {BLUE};
            border-radius: 7px;
            padding: .65rem .8rem;
            background: rgba(0, 40, 104, .04);
        }}

        @media (max-width: 640px) {{

            .nfhl-draftboard-hero {{
                gap: 14px;
                min-height: 102px;
                padding: 18px 16px;
            }}

            .nfhl-draftboard-logo {{
                width: 62px;
                height: 62px;

                font-size: 1.10rem;
            }}

            .nfhl-draftboard-kicker {{
                font-size: 0.80rem;
                letter-spacing: 0.12em;
                margin-bottom: 6px;
            }}

            .nfhl-draftboard-title {{
                font-size: clamp(
                    1.9rem,
                    9vw,
                    2.8rem
                );
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_banner(
    season_year: int,
) -> None:
    banner_html = (
        '<div class="nfhl-draftboard-hero-shell">'
        '<div class="nfhl-draftboard-hero">'

        '<div class="nfhl-draftboard-logo">'
        'NFHL'
        '</div>'

        '<div class="nfhl-draftboard-title-wrap">'

        '<div class="nfhl-draftboard-kicker">'
        'Official Draft Board'
        '</div>'

        '<div class="nfhl-draftboard-title">'
        'Draft Board '
        f'<span class="nfhl-draftboard-title-year">'
        f'{season_year}'
        '</span>'
        '</div>'

        '</div>'
        '</div>'
        '</div>'
    )

    st.markdown(
        banner_html,
        unsafe_allow_html=True,
    )


def player_dataframe(
    players: list[dict],
) -> pd.DataFrame:
    df = pd.DataFrame(players)

    if df.empty:
        return df

    numeric_columns = [
        "rank_value",
        "percent_owned",
        "percent_drafted",
        "preseason_percent_drafted",
        "gp",
        "g",
        "a",
        "pim",
        "ppp",
        "shp",
        "sog",
        "hit",
        "blk",
        "w",
        "ga",
        "sv",
        "sho",
        "nfhl_fpts",
        "nfhl_fpts_per_game",
    ]

    for column in numeric_columns:
        if column in df.columns:
            df[column] = pd.to_numeric(
                df[column],
                errors="coerce",
            )

    df["status_display"] = (
        df["player_status"]
        .fillna("Active")
    )

    df["eligible_display"] = (
        df["eligible_positions"]
        .apply(
            lambda value: (
                ", ".join(value)
                if isinstance(value, list)
                else ""
            )
        )
    )

    df["percent_drafted_display"] = (
        df["percent_drafted"] * 100.0
    )

    return df


def render_teams(
    teams: list[dict],
    target_teams: int,
    players: list[dict],
    *,
    gateway_context: dict[str, object],
) -> None:
    st.subheader("Teams")

    if not teams:
        st.info("No Yahoo teams are loaded.")
        return

    if len(teams) != target_teams:
        st.warning(
            f"Expected {target_teams} teams but found "
            f"{len(teams)}."
        )

    try:
        board_rows = get_live_draft_board()

    except Exception as exc:
        st.error(
            "Could not load finalized Draft Board team order: "
            f"{exc}"
        )
        return

    # ------------------------------------------------------------
    # CANONICAL TEAM ORDER
    # ------------------------------------------------------------

    team_lookup = {
        str(team.get("team_key") or ""): team
        for team in teams
        if str(team.get("team_key") or "")
    }

    first_round = sorted(
        (
            row
            for row in board_rows
            if int(row.get("round_number") or 0) == 1
        ),
        key=lambda row: int(
            row.get("slot_number") or 0
        ),
    )

    ordered_team_keys: list[str] = []

    for row in first_round:
        team_key = str(
            row.get("column_team_key")
            or ""
        ).strip()

        if (
            team_key
            and team_key in team_lookup
            and team_key not in ordered_team_keys
        ):
            ordered_team_keys.append(
                team_key
            )

    for team in teams:
        team_key = str(
            team.get("team_key")
            or ""
        ).strip()

        if (
            team_key
            and team_key not in ordered_team_keys
        ):
            ordered_team_keys.append(
                team_key
            )

    ordered_teams = [
        team_lookup[team_key]
        for team_key in ordered_team_keys
        if team_key in team_lookup
    ]

    # Logged-in managers see their own team first. The remaining
    # teams retain the finalized Draft Board order exactly.
    gateway_role = str(
        gateway_context.get(
            "role"
        )
        or "public"
    ).strip().lower()

    gateway_team_key = str(
        gateway_context.get(
            "team_key"
        )
        or ""
    ).strip()

    if (
        gateway_role == "manager"
        and gateway_team_key
        and gateway_team_key in team_lookup
    ):
        manager_team = (
            team_lookup[
                gateway_team_key
            ]
        )

        ordered_teams = [
            manager_team,
            *[
                team
                for team
                in ordered_teams
                if str(
                    team.get(
                        "team_key"
                    )
                    or ""
                ).strip()
                != gateway_team_key
            ],
        ]

    if not ordered_teams:
        st.info("No current NFHL teams are available.")
        return

    # ------------------------------------------------------------
    # PLAYER / ROSTER LOOKUPS
    # ------------------------------------------------------------

    players_by_key = {
        str(
            player.get(
                "yahoo_player_key"
            )
            or ""
        ): player
        for player in players
        if player.get(
            "yahoo_player_key"
        )
    }

    roster_rows_by_team: dict[
        str,
        list[dict],
    ] = {}

    for board_row in board_rows:
        if (
            not board_row.get(
                "selected_at_utc"
            )
            or not board_row.get(
                "yahoo_player_key"
            )
        ):
            continue

        owner_team_key = str(
            board_row.get(
                "current_owner_team_key"
            )
            or board_row.get(
                "column_team_key"
            )
            or ""
        ).strip()

        if not owner_team_key:
            continue

        roster_rows_by_team.setdefault(
            owner_team_key,
            [],
        ).append(
            board_row
        )

    stat_years: list[int] = []

    for player in players:
        value = player.get(
            "stats_season_year"
        )

        try:
            if value is not None:
                stat_years.append(
                    int(value)
                )
        except Exception:
            pass

    prior_year = (
        max(stat_years)
        if stat_years
        else get_season_year() - 1
    )

    fpts_label = (
        f"{prior_year} NFHL FPTS"
    )

    # ------------------------------------------------------------
    # DISPLAY HELPERS
    # ------------------------------------------------------------

    def _eligible_display(
        player: dict,
    ) -> str:
        eligible = player.get(
            "eligible_positions"
        )

        if isinstance(
            eligible,
            (list, tuple),
        ):
            return "/".join(
                str(pos)
                for pos in eligible
                if str(pos).strip()
            )

        return str(
            eligible or ""
        )

    def _number(
        value: object,
        decimals: int = 0,
    ) -> str:
        if value is None or value == "":
            return ""

        try:
            numeric = float(value)

        except Exception:
            return str(value)

        if decimals == 0:
            return str(
                int(round(numeric))
            )

        return (
            f"{numeric:.{decimals}f}"
        )

    def _percent(
        value: object,
    ) -> str:
        if value is None or value == "":
            return ""

        try:
            numeric = float(value)

        except Exception:
            return str(value)

        if 0.0 <= numeric <= 1.0:
            numeric *= 100.0

        return f"{numeric:.0f}%"

    def _group_for(
        player: dict,
        board_row: dict,
    ) -> str:
        primary = str(
            player.get(
                "primary_position"
            )
            or board_row.get(
                "selected_primary_position"
            )
            or ""
        ).upper()

        eligible_raw = player.get(
            "eligible_positions"
        )

        eligible = {
            str(pos).upper()
            for pos in (
                eligible_raw
                if isinstance(
                    eligible_raw,
                    (list, tuple),
                )
                else []
            )
        }

        position_type = str(
            player.get(
                "position_type"
            )
            or ""
        ).upper()

        if (
            primary == "G"
            or "G" in eligible
            or position_type == "G"
        ):
            return "Goalies"

        if (
            primary == "D"
            or "D" in eligible
        ):
            return "Defense"

        return "Forwards"

    def _base_row(
        board_row: dict,
        player: dict,
    ) -> dict[str, str]:
        return {
            "Player": str(
                player.get(
                    "full_name"
                )
                or board_row.get(
                    "selected_player_name"
                )
                or ""
            ),
            "NHL": str(
                player.get(
                    "nhl_team_abbr"
                )
                or board_row.get(
                    "selected_nhl_team_abbr"
                )
                or ""
            ),
            "Pos": str(
                player.get(
                    "primary_position"
                )
                or board_row.get(
                    "selected_primary_position"
                )
                or ""
            ),
            "Eligible": (
                _eligible_display(
                    player
                )
            ),
            "Rank": _number(
                player.get(
                    "rank_value"
                )
            ),
            "% Ros": _percent(
                player.get(
                    "percent_owned"
                )
            ),
            fpts_label: _number(
                player.get(
                    "nfhl_fpts"
                ),
                1,
            ),
            "FPTS/GP": _number(
                player.get(
                    "nfhl_fpts_per_game"
                ),
                2,
            ),
            "GP": _number(
                player.get(
                    "gp"
                )
            ),
        }

    def _table_rows(
        rows: list[
            tuple[
                dict,
                dict,
            ]
        ],
        *,
        goalie: bool,
    ) -> list[dict[str, str]]:
        result: list[
            dict[str, str]
        ] = []

        for board_row, player in rows:
            row = _base_row(
                board_row,
                player,
            )

            if goalie:
                row.update(
                    {
                        "W": _number(
                            player.get("w")
                        ),
                        "GA": _number(
                            player.get("ga")
                        ),
                        "SV": _number(
                            player.get("sv")
                        ),
                        "SHO": _number(
                            player.get("sho")
                        ),
                    }
                )

            else:
                row.update(
                    {
                        "G": _number(
                            player.get("g")
                        ),
                        "A": _number(
                            player.get("a")
                        ),
                        "PIM": _number(
                            player.get("pim")
                        ),
                        "PPP": _number(
                            player.get("ppp")
                        ),
                        "SHP": _number(
                            player.get("shp")
                        ),
                        "SOG": _number(
                            player.get("sog")
                        ),
                        "HIT": _number(
                            player.get("hit")
                        ),
                        "BLK": _number(
                            player.get("blk")
                        ),
                    }
                )

            result.append(
                row
            )

        def sort_key(
            item: dict[str, str],
        ) -> tuple[
            bool,
            float,
            str,
        ]:
            raw = item.get(
                fpts_label
            )

            try:
                points = float(raw)

            except Exception:
                points = None

            return (
                points is None,
                -points
                if points is not None
                else 0.0,
                item.get(
                    "Player",
                    "",
                ).casefold(),
            )

        result.sort(
            key=sort_key
        )

        return result

    st.markdown(
        """
        <style>
          div[data-testid="stMarkdownContainer"]:has(table.nfhl-team-table) {
              width: 100%;
              max-width: 100%;
              overflow-x: auto;
              -webkit-overflow-scrolling: touch;
          }

          table.nfhl-team-table {
              width: 100%;
              border-collapse: separate;
              border-spacing: 0;
              font-size: 0.86rem;
              margin: 0.40rem 0 1.25rem 0;
              border: 1px solid #8EA5C4;
              border-radius: 8px;
              overflow: hidden;
              background: #FFFFFF;
              color: #0F172A;
          }

          table.nfhl-team-table th {
              text-align: center;
              background: #0B234A;
              color: #FFFFFF;
              font-weight: 800;
              border-bottom: 2px solid #334E73;
              padding: 0.48rem 0.56rem;
              white-space: nowrap;
          }

          table.nfhl-team-table td {
              text-align: center;
              color: #0F172A;
              background: #FFFFFF;
              border-bottom: 1px solid #CBD5E1;
              padding: 0.40rem 0.56rem;
              white-space: nowrap;
          }

          table.nfhl-team-table tr:nth-child(even) td {
              background: #EEF3F8;
          }

          table.nfhl-team-table tr:hover td {
              background: #DCE8F5;
          }

          table.nfhl-team-table th:first-child,
          table.nfhl-team-table td:first-child {
              text-align: left;
              font-weight: 700;
              min-width: 11rem;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.caption(
        "Skaters: G 4.0 • A 2.5 • PIM 0.2 • PPP 1.0 • "
        "SHP 1.25 • SOG 0.25 • HIT 0.5 • BLK 0.5  |  "
        "Goalies: W 3.0 • GA -1.0 • SV 0.25 • SHO 2.5"
    )

    tabs = st.tabs(
        [
            str(
                team.get(
                    "team_name"
                )
                or "Team"
            )
            for team in ordered_teams
        ]
    )

    for tab, team in zip(
        tabs,
        ordered_teams,
    ):
        with tab:
            team_key = str(
                team.get(
                    "team_key"
                )
                or ""
            )

            team_name = str(
                team.get(
                    "team_name"
                )
                or ""
            )

            owner_name = str(
                team.get(
                    "owner_name"
                )
                or ""
            )

            drafted_rows = (
                roster_rows_by_team.get(
                    team_key,
                    [],
                )
            )

            st.markdown(
                f"### {team_name}"
            )

            st.caption(
                f"Manager: {owner_name}  •  "
                f"Drafted: {len(drafted_rows)} / 18"
            )

            grouped: dict[
                str,
                list[
                    tuple[
                        dict,
                        dict,
                    ]
                ],
            ] = {
                "Forwards": [],
                "Defense": [],
                "Goalies": [],
            }

            for board_row in drafted_rows:
                player_key = str(
                    board_row.get(
                        "yahoo_player_key"
                    )
                    or ""
                )

                player = (
                    players_by_key.get(
                        player_key,
                        {},
                    )
                )

                group = _group_for(
                    player,
                    board_row,
                )

                grouped[
                    group
                ].append(
                    (
                        board_row,
                        player,
                    )
                )

            for group_name in (
                "Forwards",
                "Defense",
                "Goalies",
            ):
                st.markdown(
                    f"#### {group_name}"
                )

                rows = grouped[
                    group_name
                ]

                if not rows:
                    st.caption(
                        "No players drafted yet."
                    )
                    continue

                table_rows = (
                    _table_rows(
                        rows,
                        goalie=(
                            group_name
                            == "Goalies"
                        ),
                    )
                )

                table_df = (
                    pd.DataFrame(
                        table_rows
                    )
                )

                st.markdown(
                    table_df.to_html(
                        index=False,
                        escape=True,
                        classes=(
                            "nfhl-team-table"
                        ),
                    ),
                    unsafe_allow_html=True,
                )


def render_players(
    players: list[dict],
    prior_year: int,
) -> None:
    st.subheader("Available Players")

    st.caption(
        "Current-season Yahoo Fantasy Hockey player universe. "
        f"{prior_year} statistics are supplemental only."
    )

    try:
        board_rows = get_live_draft_board()

    except Exception as exc:
        st.error(
            "Could not load current NFHL roster status: "
            f"{exc}"
        )
        return

    df = player_dataframe(players)

    if df.empty:
        st.warning("No current Yahoo players are loaded.")
        return

    if "yahoo_player_key" not in df.columns:
        st.error(
            "Current Yahoo player data is missing "
            "yahoo_player_key."
        )
        return

    total_slots = max(
        (
            int(
                row.get(
                    "slot_number"
                )
                or 0
            )
            for row in board_rows
        ),
        default=0,
    )

    drafted_status_by_key: dict[
        str,
        str,
    ] = {}

    if total_slots > 0:
        for board_row in board_rows:
            player_key = str(
                board_row.get(
                    "yahoo_player_key"
                )
                or ""
            ).strip()

            if (
                not player_key
                or not board_row.get(
                    "selected_at_utc"
                )
            ):
                continue

            round_number = int(
                board_row.get(
                    "round_number"
                )
                or 0
            )

            slot_number = int(
                board_row.get(
                    "slot_number"
                )
                or 0
            )

            pick_number = (
                slot_number
                if round_number % 2 == 1
                else (
                    total_slots
                    + 1
                    - slot_number
                )
            )

            owner_name = str(
                board_row.get(
                    "current_owner_team_name"
                )
                or board_row.get(
                    "column_team_name"
                )
                or "Drafted"
            ).strip()

            drafted_status_by_key[
                player_key
            ] = (
                f"{owner_name} "
                f"({round_number}.{pick_number})"
            )

    df["roster_status_display"] = (
        df["yahoo_player_key"]
        .fillna("")
        .astype(str)
        .map(
            lambda player_key: (
                drafted_status_by_key.get(
                    player_key,
                    "Available",
                )
            )
        )
    )

    df["is_nfhl_drafted"] = (
        df["yahoo_player_key"]
        .fillna("")
        .astype(str)
        .isin(
            drafted_status_by_key
        )
    )

    row1, row2, row3, row4, row5 = st.columns(
        [
            2.2,
            1.20,
            1.20,
            1.20,
            1.65,
        ]
    )

    search = row1.text_input(
        "Search",
        placeholder="Player or NHL team",
    )

    positions = row2.multiselect(
        "Position",
        [
            "C",
            "LW",
            "RW",
            "D",
            "G",
        ],
    )

    nhl_teams = sorted(
        str(value)
        for value in df["nhl_team_abbr"]
        .dropna()
        .unique()
        if str(value).strip()
    )

    nhl_team = row3.selectbox(
        "NHL Team",
        ["All"] + nhl_teams,
    )

    status_options = sorted(
        str(value)
        for value in df["status_display"]
        .dropna()
        .unique()
    )

    status = row4.selectbox(
        "Status",
        ["All"] + status_options,
    )

    roster_status = row5.selectbox(
        "Roster Status",
        [
            "All Players",
            "All Available Players",
        ],
        index=1,
    )

    filtered = df.copy()

    if (
        roster_status
        == "All Available Players"
    ):
        filtered = filtered[
            ~filtered[
                "is_nfhl_drafted"
            ]
        ]

    if search.strip():
        query = search.strip()

        mask = (
            filtered["full_name"]
            .fillna("")
            .str.contains(
                query,
                case=False,
                regex=False,
            )
            |
            filtered["nhl_team_abbr"]
            .fillna("")
            .str.contains(
                query,
                case=False,
                regex=False,
            )
        )

        filtered = filtered[mask]

    if positions:
        filtered = filtered[
            filtered[
                "primary_position"
            ].isin(positions)
        ]

    if nhl_team != "All":
        filtered = filtered[
            filtered["nhl_team_abbr"]
            == nhl_team
        ]

    if status != "All":
        filtered = filtered[
            filtered["status_display"]
            == status
        ]


    display = filtered[
        [
            "rank_value",
            "full_name",
            "nhl_team_abbr",
            "primary_position",
            "eligible_display",
            "status_display",
            "roster_status_display",

            "nfhl_fpts",
            "nfhl_fpts_per_game",
            "gp",

            "g",
            "a",
            "pim",
            "ppp",
            "shp",
            "sog",
            "hit",
            "blk",

            "w",
            "ga",
            "sv",
            "sho",

            "percent_owned",
            "percent_drafted_display",
        ]
    ].copy()

    display.columns = [
        "Rank",
        "Player",
        "NHL",
        "Pos",
        "Eligible",
        "Status",
        "Roster Status",

        f"{prior_year} NFHL FPTS",
        "FPTS/GP",
        "GP",

        "G",
        "A",
        "PIM",
        "PPP",
        "SHP",
        "SOG",
        "HIT",
        "BLK",

        "W",
        "GA",
        "SV",
        "SHO",

        "% Owned",
        "% Drafted",
    ]

    st.dataframe(
        display,
        hide_index=True,
        use_container_width=True,
        height=650,
        column_config={
            "Rank":
                st.column_config.NumberColumn(
                    format="%.0f",
                ),

            f"{prior_year} NFHL FPTS":
                st.column_config.NumberColumn(
                    format="%.1f",
                ),

            "FPTS/GP":
                st.column_config.NumberColumn(
                    format="%.2f",
                ),

            "% Owned":
                st.column_config.NumberColumn(
                    format="%.0f%%",
                ),

            "% Drafted":
                st.column_config.NumberColumn(
                    format="%.1f%%",
                ),
        },
    )

    st.caption(
        "Players without prior-season history remain fully "
        "eligible because current-season Yahoo membership is authoritative."
    )



def render_draft_lottery(
    current_teams: int,
    target_teams: int,
    *,
    is_commissioner: bool,
) -> None:
    league_ready = (
        current_teams == target_teams
    )

    if not league_ready:
        st.warning(
            "Lottery Locked — exactly "
            f"{target_teams} real Yahoo teams are required. "
            f"Current league membership: "
            f"{current_teams}/{target_teams}."
        )
    else:
        equal_odds = (
            100.0 / target_teams
            if target_teams
            else 0.0
        )

        st.caption(
            "All NFHL teams receive equal odds "
            f"({equal_odds:.2f}% per draft slot). "
            "The complete random order is persisted "
            "before the first result is revealed."
        )

    lottery = load_lottery_state()

    if lottery is None:
        st.info(
            "No NFHL Draft Lottery has been initialized."
        )

        st.caption(
            "Lottery administration is available "
            "from the Commissioner View."
        )

        return

    render_nfhl_lottery_board(
        lottery=lottery,
        is_commissioner=is_commissioner,
        key_prefix=(
            "nfhl_commissioner_lottery"
            if is_commissioner
            else "nfhl_public_lottery"
        ),
    )

    if is_commissioner:
        st.caption(
            "Commissioner View — reveal the next pick "
            "directly from its lottery card."
        )
    else:
        st.caption(
            "Lottery results are read-only on the "
            "public DraftBoard."
        )



# NFHL_AUTOPICK_PANEL_START
# ================================================================
# NFHL DRAFT QUEUE / AUTO-PICK PANEL
# ================================================================


def render_autopick_panel(
    *,
    gateway_context: dict[str, object],
    teams: list[dict],
    players: list[dict],
    draft_status: str,
) -> None:
    role = str(
        gateway_context.get("role")
        or "public"
    ).strip().lower()

    if role not in {
        "manager",
        "commissioner",
    }:
        return

    with st.expander(
        "Draft Queue & Auto-Pick",
        expanded=bool(
            st.session_state.pop(
                "nfhl_force_open_autopick",
                False,
            )
        ),
    ):
        _render_autopick_panel_contents(
            gateway_context=gateway_context,
            teams=teams,
            players=players,
            draft_status=draft_status,
        )


def _render_autopick_panel_contents(
    *,
    gateway_context: dict[str, object],
    teams: list[dict],
    players: list[dict],
    draft_status: str,
) -> None:
    role = str(
        gateway_context.get("role")
        or "public"
    ).strip().lower()

    # Public visitors remain read-only and do not receive
    # manager queue controls.
    if role not in {
        "manager",
        "commissioner",
    }:
        return

    st.caption(
        "Rank up to five players for your next pick. "
        "Queue changes are allowed before the draft. "
        "Auto-Pick itself remains off until explicitly armed."
    )

    # NFHL_AUTOPICK_GRACE_UI
    st.caption(
        "When an armed pick becomes current, Auto-Pick waits "
        "3:00 of active clock time before unattended execution. "
        "Paused time does not count. A successful Auto-Pick "
        "automatically turns itself OFF."
    )

    team_lookup = {
        str(team["team_key"]): team
        for team in teams
        if team.get("team_key")
    }

    # ------------------------------------------------------------
    # RESOLVE TEAM
    # ------------------------------------------------------------

    if role == "manager":
        selected_team_key = str(
            gateway_context.get(
                "team_key"
            )
            or ""
        ).strip()

        if (
            not selected_team_key
            or selected_team_key
            not in team_lookup
        ):
            st.error(
                "Your remembered NFHL team is not "
                "available in the current league."
            )
            return

        selected_team = team_lookup[
            selected_team_key
        ]

        st.caption(
            "Managing queue for "
            f"**{selected_team.get('team_name', '')}**."
        )

    else:
        if not team_lookup:
            st.info(
                "No current NFHL teams are available."
            )
            return

        team_keys = list(
            team_lookup.keys()
        )

        selected_team_key = st.selectbox(
            "Commissioner — Manage Team",
            options=team_keys,
            format_func=lambda key: (
                f"{team_lookup[key].get('team_name', '')} "
                f"— {team_lookup[key].get('owner_name', '')}"
            ),
            key="nfhl_autopick_commissioner_team",
            on_change=lambda: (
                st.session_state.__setitem__(
                    "nfhl_force_open_autopick",
                    True,
                )
            ),
        )

        selected_team = team_lookup[
            selected_team_key
        ]

    # ------------------------------------------------------------
    # READ CURRENT STATE
    # ------------------------------------------------------------

    try:
        autopick_state = get_autopick_state(
            selected_team_key
        )

        open_picks = get_autopick_open_picks(
            selected_team_key
        )

    except Exception as exc:
        st.error(
            "Unable to load Draft Queue / Auto-Pick state: "
            f"{exc}"
        )
        return

    queue_rows = (
        autopick_state.get("queue")
        or []
    )

    enabled = bool(
        autopick_state.get(
            "enabled"
        )
    )

    armed_pick_id = (
        autopick_state.get(
            "armed_pick_id"
        )
    )

    next_pick = (
        open_picks[0]
        if open_picks
        else None
    )

    # ------------------------------------------------------------
    # PLAYER OPTIONS
    # ------------------------------------------------------------

    player_lookup: dict[str, dict] = {}

    player_keys: list[str] = []

    try:
        board_rows = get_live_draft_board()
    except Exception as exc:
        st.error(
            "Unable to load current NFHL "
            "player availability: "
            f"{exc}"
        )
        return

    drafted_player_keys = {
        str(
            board_row.get(
                "yahoo_player_key"
            )
            or ""
        ).strip()
        for board_row in board_rows
        if (
            board_row.get(
                "selected_at_utc"
            )
            and str(
                board_row.get(
                    "yahoo_player_key"
                )
                or ""
            ).strip()
        )
    }

    for player in players:
        player_key = str(
            player.get(
                "yahoo_player_key"
            )
            or ""
        ).strip()

        if not player_key:
            continue

        player_lookup[
            player_key
        ] = player

        if player_key in drafted_player_keys:
            continue

        player_keys.append(
            player_key
        )

    def player_label(
        player_key: str,
    ) -> str:
        if not player_key:
            return "— Empty —"

        player = player_lookup.get(
            player_key
        )

        if not player:
            return player_key

        rank = player.get(
            "rank_value"
        )

        try:
            rank_text = (
                f"#{int(float(rank))}"
                if rank is not None
                else "NR"
            )
        except Exception:
            rank_text = "NR"

        name = str(
            player.get(
                "full_name"
            )
            or ""
        )

        nhl = str(
            player.get(
                "nhl_team_abbr"
            )
            or "-"
        )

        pos = str(
            player.get(
                "primary_position"
            )
            or "-"
        )

        return (
            f"{rank_text} — "
            f"{name} — {nhl} — {pos}"
        )

    options = [
        ""
    ] + player_keys

    effective_queue_rows = sorted(
        (
            row
            for row in queue_rows
            if (
                str(
                    row.get(
                        "yahoo_player_key"
                    )
                    or ""
                ).strip()
                and str(
                    row.get(
                        "yahoo_player_key"
                    )
                    or ""
                ).strip()
                not in drafted_player_keys
            )
        ),
        key=lambda row: int(
            row.get(
                "queue_rank"
            )
            or 0
        ),
    )

    current_by_rank = {
        display_rank:
        str(
            row["yahoo_player_key"]
        )
        for display_rank, row in enumerate(
            effective_queue_rows,
            start=1,
        )
    }

    # ------------------------------------------------------------
    # QUEUE EDITOR / STAGED AUTO-PICK SETTINGS
    # ------------------------------------------------------------

    st.markdown(
        "**Priority Queue**"
    )

    selections: list[str] = []

    for rank in range(
        1,
        6,
    ):
        current_key = (
            current_by_rank.get(
                rank,
                "",
            )
        )

        try:
            current_index = (
                options.index(
                    current_key
                )
            )
        except ValueError:
            current_index = 0

        selected = st.selectbox(
            f"#{rank}",
            options=options,
            index=current_index,
            format_func=player_label,
            key=(
                f"nfhl_autopick_"
                f"{selected_team_key}_"
                f"rank_{rank}"
            ),
        )

        selections.append(
            selected
        )

    normalized = [
        player_key
        for player_key
        in selections
        if player_key
    ]

    saved_queue_keys = [
        str(
            row["yahoo_player_key"]
        )
        for row in effective_queue_rows
    ]

    queue_dirty = (
        normalized
        != saved_queue_keys
    )

    actor = (
        "commissioner_link"
        if role == "commissioner"
        else (
            "manager:"
            + selected_team_key
        )
    )

    draft_active = (
        str(
            draft_status
        ).upper()
        == "ACTIVE"
    )

    can_stage_arm = (
        draft_active
        and next_pick is not None
        and bool(normalized)
    )

    # Use the database control timestamp as the widget version.
    # Database mutations therefore receive a new widget identity,
    # while unsaved queue edits preserve the manager's staged choice.
    toggle_version = str(
        autopick_state.get(
            "updated_at_utc"
        )
        or "initial"
    )

    toggle_key = (
        "nfhl_autopick_enabled_"
        + selected_team_key
        + "_"
        + toggle_version
    )

    requested_enabled = st.toggle(
        "Auto-Pick",
        value=bool(enabled),
        key=toggle_key,
        disabled=(
            not enabled
            and not can_stage_arm
        ),
        help=(
            "Choose whether Auto-Pick should be ON "
            "after Save Queue. Saving commits both "
            "the ranked queue and this setting in "
            "one manager action. Auto-Pick remains "
            "limited to this team's exact next open "
            "pick and retains the configured "
            "3-minute active-clock grace period."
        ),
    )

    settings_dirty = (
        queue_dirty
        or requested_enabled
        != bool(enabled)
    )

    if settings_dirty:
        st.caption(
            "Unsaved queue / Auto-Pick changes."
        )

        st.markdown(
            """
            <style>
            .st-key-nfhl_autopick_save_pending button {
                background-color: #C62828 !important;
                border-color: #C62828 !important;
                color: #FFFFFF !important;
            }

            .st-key-nfhl_autopick_save_pending button:hover {
                background-color: #B71C1C !important;
                border-color: #B71C1C !important;
                color: #FFFFFF !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

    if settings_dirty:
        save_container_key = (
            "nfhl_autopick_save_pending"
        )
        save_button_type = "primary"
    else:
        save_container_key = (
            "nfhl_autopick_save_clean"
        )
        save_button_type = "secondary"

    with st.container(
        key=save_container_key
    ):
        save_queue = st.button(
            "Save Queue",
            type=save_button_type,
            use_container_width=True,
            key=(
                "nfhl_autopick_save_"
                + selected_team_key
            ),
        )

    if settings_dirty:
        if requested_enabled:
            if can_stage_arm:
                st.caption(
                    "Save Queue will save these rankings "
                    "and turn Auto-Pick ON for "
                    f"`{next_pick['pick_id']}`."
                )
            else:
                st.warning(
                    "Auto-Pick cannot be enabled with "
                    "the current unsaved settings."
                )
        else:
            st.caption(
                "Save Queue will save these rankings "
                "with Auto-Pick OFF."
            )

    elif enabled:
        st.success(
            "Auto-Pick is ON for "
            f"`{armed_pick_id}`."
        )

    elif not draft_active:
        st.info(
            "Your queue can be prepared now. "
            "Auto-Pick becomes available when "
            "the NFHL draft is ACTIVE."
        )

    elif next_pick is None:
        st.info(
            "No open draft pick exists for this team."
        )

    elif not normalized:
        st.info(
            "Add at least one player to the queue "
            "before enabling Auto-Pick."
        )

    else:
        st.caption(
            "Auto-Pick is OFF."
        )

    if not save_queue:
        return

    if (
        len(normalized)
        != len(set(normalized))
    ):
        st.error(
            "A player can appear only once "
            "in the queue."
        )
        return

    if (
        requested_enabled
        and not can_stage_arm
    ):
        st.error(
            "Auto-Pick cannot be enabled "
            "for this team right now."
        )
        return

    try:
        save_autopick_queue(
            team_key=(
                selected_team_key
            ),
            player_keys=(
                normalized
            ),
            actor=actor,
        )

    except Exception as exc:
        st.error(
            "Unable to save queue: "
            f"{exc}"
        )
        return

    # save_autopick_queue intentionally leaves Auto-Pick OFF.
    # If the manager requested ON, re-arm only through the existing
    # validated database helper. A failure therefore leaves the safe
    # state: queue saved, Auto-Pick OFF.
    if requested_enabled:
        try:
            arm_autopick(
                team_key=(
                    selected_team_key
                ),
                pick_id=str(
                    next_pick[
                        "pick_id"
                    ]
                ),
                actor=actor,
            )

        except Exception as exc:
            st.error(
                "Queue was saved, but Auto-Pick "
                "could not be enabled. "
                "Auto-Pick remains OFF: "
                f"{exc}"
            )

            st.session_state[
                "nfhl_force_open_autopick"
            ] = True

            return

        st.success(
            "Draft queue saved and Auto-Pick "
            "enabled for "
            f"`{next_pick['pick_id']}`."
        )

    else:
        st.success(
            "Draft queue saved. "
            "Auto-Pick is OFF."
        )

    st.session_state[
        "nfhl_force_open_autopick"
    ] = True

    st.rerun()


# NFHL_AUTOPICK_PANEL_END





# NFHL_DRAFT_LIFECYCLE_UI_START
# ================================================================
# NFHL DRAFT LIFECYCLE / CLOCK UI
# ================================================================


def _format_nfhl_seconds(
    seconds: int | None,
) -> str:

    if seconds is None:
        return "—"

    seconds = max(
        int(seconds),
        0,
    )

    days, remainder = divmod(
        seconds,
        86400,
    )

    hours, remainder = divmod(
        remainder,
        3600,
    )

    minutes, secs = divmod(
        remainder,
        60,
    )

    if days:
        return (
            f"{days}d "
            f"{hours:02d}:"
            f"{minutes:02d}:"
            f"{secs:02d}"
        )

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{secs:02d}"
    )


def render_draft_lifecycle_panel(
    *,
    gateway_context: dict[str, object],
    show_status: bool = True,
    show_commissioner_controls: bool = True,
) -> None:

    role = str(
        gateway_context.get("role")
        or "public"
    ).strip().lower()

    try:
        snapshot = (
            get_draft_clock_snapshot()
        )

        clock_config = (
            get_draft_clock_config()
        )

        configured_seconds = int(
            clock_config.get(
                "seconds_per_pick"
            )
            or 86400
        )

        configured_hours = max(
            1,
            configured_seconds // 3600,
        )

        clock_ready = bool(
            clock_config.get(
                "configured"
            )
        ) and (
            configured_seconds > 0
        ) and bool(
            clock_config.get(
                "auto_advance"
            )
        ) and bool(
            clock_config.get(
                "weekends_count"
            )
        )

        board_rows = (
            get_live_draft_board()
        )

    except Exception as exc:
        st.error(
            "Unable to load NFHL draft clock status: "
            f"{exc}"
        )
        return

    status = str(
        snapshot.get("status")
        or ""
    ).upper()

    board_initialized = bool(
        board_rows
    )

    # ------------------------------------------------------------
    # LIVE CLOCK — ALL USERS
    # ------------------------------------------------------------

    if (
        show_status
        and board_initialized
        and status == "ACTIVE"
    ):

        st.markdown(
            "### Draft Clock"
        )

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "Clock",
            (
                "RUNNING"
                if snapshot.get(
                    "is_running"
                )
                else "PAUSED"
            ),
        )

        c2.metric(
            "Pick Window",
            _format_nfhl_seconds(
                configured_seconds
            ),
        )

        c3.metric(
            "Elapsed",
            _format_nfhl_seconds(
                snapshot.get(
                    "elapsed_seconds"
                )
            ),
        )

        c4.metric(
            "Remaining",
            _format_nfhl_seconds(
                snapshot.get(
                    "remaining_seconds"
                )
            ),
        )

    if (
        role != "commissioner"
        or not show_commissioner_controls
    ):
        return

    # ------------------------------------------------------------
    # COMMISSIONER CONTROLS
    # ------------------------------------------------------------

    with st.expander(
        "Draft Operations",
        expanded=bool(
            st.session_state.pop(
                "nfhl_force_open_draft_operations",
                False,
            )
        ),
    ):

        st.markdown(
            "#### NFHL Slow Draft Clock"
        )

        st.write(
            "**Pick duration:** "
            f"{configured_hours} hour"
            f"{'s' if configured_hours != 1 else ''} per pick"
        )

        st.write(
            "**Reminders:** automatically scaled "
            "to the pick duration"
        )

        st.write(
            "**Expiration:** the missed pick stays open, "
            "while the next manager immediately goes on the clock"
        )

        st.write(
            "**Weekends:** counted"
        )

        if status == "PREP":

            default_duration = (
                "24 hours"
                if configured_seconds == 86400
                else (
                    "12 hours"
                    if configured_seconds == 43200
                    else "Custom"
                )
            )

            with st.form(
                "nfhl_clock_rule_form",
                clear_on_submit=False,
            ):
                duration_choice = st.radio(
                    "Pick duration",
                    options=[
                        "24 hours",
                        "12 hours",
                        "Custom",
                    ],
                    index=[
                        "24 hours",
                        "12 hours",
                        "Custom",
                    ].index(
                        default_duration
                    ),
                    horizontal=True,
                )

                custom_hours = st.number_input(
                    "Custom hours",
                    min_value=1,
                    max_value=72,
                    value=max(
                        1,
                        min(
                            72,
                            configured_hours,
                        ),
                    ),
                    step=1,
                )

                save_clock = (
                    st.form_submit_button(
                        "Save Clock Rule",
                        type="primary",
                        use_container_width=True,
                    )
                )

            if save_clock:

                new_hours = (
                    24
                    if duration_choice == "24 hours"
                    else (
                        12
                        if duration_choice == "12 hours"
                        else int(custom_hours)
                    )
                )

                new_seconds = (
                    new_hours * 3600
                )

                if new_hours == 24:
                    reminders = [
                        12 * 3600,
                        6 * 3600,
                        1 * 3600,
                    ]

                elif new_hours == 12:
                    reminders = [
                        6 * 3600,
                        3 * 3600,
                        1 * 3600,
                    ]

                else:
                    reminders = sorted(
                        {
                            new_seconds // 2,
                            new_seconds // 4,
                            3600,
                        },
                        reverse=True,
                    )

                    reminders = [
                        seconds
                        for seconds in reminders
                        if (
                            0
                            < seconds
                            < new_seconds
                        )
                    ]

                try:
                    from draftboard.data.db import (
                        save_draft_clock_config,
                    )

                    save_draft_clock_config(
                        seconds_per_pick=(
                            new_seconds
                        ),
                        reminder_seconds=(
                            reminders
                        ),
                        actor="commissioner_link",
                    )

                except Exception as exc:
                    st.error(
                        "Clock configuration failed: "
                        f"{exc}"
                    )

                else:
                    st.cache_data.clear()
                    st.session_state["nfhl_force_open_draft_operations"] = True
                    st.rerun()

        elif clock_ready:
            st.success(
                "Draft clock configuration is active."
            )

        else:
            st.error(
                "The active draft has an invalid "
                "clock configuration."
            )

        st.divider()

        st.markdown(
            "#### Draft Lifecycle"
        )

        if not board_initialized:

            st.info(
                "The Draft Board is not initialized yet. "
                "Start Draft becomes available after Draft Setup "
                "builds the complete Draft Board."
            )

            lifecycle_cols = st.columns(3)

            with lifecycle_cols[0]:
                st.button(
                    "Start NFHL Draft",
                    type="primary",
                    use_container_width=True,
                    disabled=True,
                    key="nfhl_start_draft_board_not_ready",
                )

            with lifecycle_cols[1]:
                st.button(
                    "Pause Draft Clock",
                    use_container_width=True,
                    disabled=True,
                    key="nfhl_pause_draft_board_not_ready",
                )

            with lifecycle_cols[2]:
                st.button(
                    "Resume Draft Clock",
                    use_container_width=True,
                    disabled=True,
                    key="nfhl_resume_draft_board_not_ready",
                )

            st.caption(
                "Start Draft requires the completed Draft Board. "
                "Pause and Resume become available after the draft starts."
            )

            return

        if status == "PREP":

            if not clock_ready:
                st.warning(
                    "Configure a valid draft clock "
                    "before starting the draft."
                )

            confirm_start = st.checkbox(
                "I confirm the Draft Board is correct "
                "and want to start the NFHL draft.",
                key="nfhl_confirm_start_draft",
            )

            lifecycle_cols = st.columns(3)

            with lifecycle_cols[0]:
                if st.button(
                    "Start NFHL Draft",
                    type="primary",
                    use_container_width=True,
                    disabled=(
                        not clock_ready
                        or not confirm_start
                    ),
                    key="nfhl_start_draft",
                ):

                    try:
                        result = start_nfhl_draft(
                            actor="commissioner_link",
                        )

                    except Exception as exc:
                        st.error(
                            "Draft was not started: "
                            f"{exc}"
                        )

                    else:
                        st.success(
                            "NFHL draft started at "
                            f"{result['current_pick_id']}."
                        )

                        st.cache_data.clear()
                        st.session_state["nfhl_force_open_draft_operations"] = True
                        st.rerun()

            with lifecycle_cols[1]:
                st.button(
                    "Pause Draft Clock",
                    use_container_width=True,
                    disabled=True,
                    key="nfhl_pause_draft_not_started",
                )

            with lifecycle_cols[2]:
                st.button(
                    "Resume Draft Clock",
                    use_container_width=True,
                    disabled=True,
                    key="nfhl_resume_draft_not_started",
                )

            st.caption(
                "Pause becomes available once the draft is running. "
                "Resume becomes available when an active draft is paused."
            )

            return

        if status != "ACTIVE":
            return

        st.write(
            "**Current pick:** "
            f"{snapshot.get('current_pick_id') or '—'}"
        )

        st.write(
            "**Team:** "
            f"{snapshot.get('current_team_name') or '—'}"
        )

        st.write(
            "**Clock status:** "
            + (
                "RUNNING"
                if snapshot.get(
                    "is_running"
                )
                else "PAUSED"
            )
        )

        c1, c2 = st.columns(2)

        if snapshot.get(
            "is_running"
        ):

            with c1:
                if st.button(
                    "Pause Draft Clock",
                    use_container_width=True,
                    key="nfhl_pause_draft",
                ):

                    try:
                        pause_nfhl_draft(
                            actor="commissioner_link",
                        )

                    except Exception as exc:
                        st.error(
                            "Draft clock was not paused: "
                            f"{exc}"
                        )

                    else:
                        st.success(
                            "NFHL draft clock paused."
                        )

                        st.cache_data.clear()
                        st.session_state["nfhl_force_open_draft_operations"] = True
                        st.rerun()

        else:

            with c1:
                if st.button(
                    "Resume Draft Clock",
                    type="primary",
                    use_container_width=True,
                    key="nfhl_resume_draft",
                ):

                    try:
                        resume_nfhl_draft(
                            actor="commissioner_link",
                        )

                    except Exception as exc:
                        st.error(
                            "Draft clock was not resumed: "
                            f"{exc}"
                        )

                    else:
                        st.success(
                            "NFHL draft clock resumed."
                        )

                        st.cache_data.clear()
                        st.session_state["nfhl_force_open_draft_operations"] = True
                        st.rerun()

        with c2:
            if st.button(
                "Reset Current Pick to League Rule",
                use_container_width=True,
                key="nfhl_reset_current_pick_clock",
            ):

                try:
                    set_nfhl_current_pick_remaining(
                        remaining_seconds=(
                            configured_seconds
                        ),
                        actor="commissioner_link",
                    )

                except Exception as exc:
                    st.error(
                        "Current-pick clock reset failed: "
                        f"{exc}"
                    )

                else:
                    st.success(
                        "Current pick reset to the full "
                        "league clock window."
                    )

                    st.cache_data.clear()
                    st.session_state["nfhl_force_open_draft_operations"] = True
                    st.rerun()

        st.divider()

        st.markdown(
            "#### Adjust Current Pick"
        )

        st.caption(
            "This changes only the current manager's remaining "
            "time. Future picks use the configured league clock. "
            "For a longer commissioner hold, pause the draft instead."
        )

        current_remaining = int(
            snapshot.get(
                "remaining_seconds"
            )
            or configured_seconds
        )

        default_hours = max(
            1,
            min(
                configured_hours,
                int(
                    (
                        current_remaining
                        + 3599
                    )
                    // 3600
                ),
            ),
        )

        remaining_hours = st.number_input(
            "Set hours remaining",
            min_value=1,
            max_value=configured_hours,
            value=default_hours,
            step=1,
            key="nfhl_adjust_remaining_hours",
        )

        if st.button(
            "Apply Current-Pick Adjustment",
            use_container_width=True,
            key="nfhl_apply_current_pick_adjustment",
        ):

            try:
                set_nfhl_current_pick_remaining(
                    remaining_seconds=(
                        int(
                            remaining_hours
                        )
                        * 3600
                    ),
                    actor="commissioner_link",
                )

            except Exception as exc:
                st.error(
                    "Current-pick adjustment failed: "
                    f"{exc}"
                )

            else:
                st.success(
                    "Current pick clock adjusted."
                )

                st.cache_data.clear()
                st.session_state["nfhl_force_open_draft_operations"] = True
                st.rerun()


# NFHL_DRAFT_LIFECYCLE_UI_END


# NFHL_DRAFT_READINESS_UI_START
# ================================================================
# COMMISSIONER — DRAFT READINESS / INITIALIZATION
# ================================================================


def render_draft_readiness_panel(
    *,
    gateway_context: dict[str, object],
    teams: list[dict],
) -> None:
    role = str(
        gateway_context.get("role")
        or "public"
    ).strip().lower()

    if role != "commissioner":
        return

    with st.expander(
        "League Setup & Roll Call",
        expanded=bool(
            st.session_state.pop(
                "nfhl_force_open_league_setup",
                False,
            )
        ),
    ):
        refresh_notice = (
            st.session_state.pop(
                "nfhl_yahoo_team_refresh_notice",
                None,
            )
        )

        if refresh_notice:
            st.success(
                str(refresh_notice)
            )

        if st.button(
            "Refresh Yahoo League Teams",
            use_container_width=True,
            key="nfhl_refresh_yahoo_teams",
        ):
            try:
                with st.spinner(
                    "Refreshing Yahoo league teams..."
                ):
                    refresh_result = (
                        refresh_yahoo_teams_live(
                            actor="commissioner_link",
                        )
                    )

                    from draftboard.data.season_team_slots import (
                        auto_match_season_team_slots,
                    )

                    auto_match_season_team_slots()

            except Exception as exc:
                st.error(
                    "Yahoo team refresh failed: "
                    f"{exc}"
                )

            else:
                st.session_state[
                    "nfhl_yahoo_team_refresh_notice"
                ] = (
                    "Yahoo team refresh complete: "
                    f"{refresh_result['yahoo_team_count']}"
                    f"/{refresh_result['target_team_count']} "
                    "real teams. "
                    f"New teams: "
                    f"{refresh_result['new_team_count']}. "
                    f"New manager links: "
                    f"{refresh_result['gateway_links_created']}."
                )

                st.cache_data.clear()
                st.session_state["nfhl_force_open_league_setup"] = True
                st.rerun()

        if st.button(
            "Refresh Yahoo Player Data",
            use_container_width=True,
            key="nfhl_refresh_yahoo_players",
        ):
            try:
                with st.spinner(
                    "Refreshing Yahoo player data..."
                ):
                    player_refresh_result = (
                        refresh_yahoo_teams_live(
                            actor="commissioner_link",
                            include_players=True,
                        )
                    )

                    from draftboard.data.season_team_slots import (
                        auto_match_season_team_slots,
                    )

                    auto_match_season_team_slots()

            except Exception as exc:
                st.error(
                    "Yahoo player refresh failed: "
                    f"{exc}"
                )

            else:
                st.session_state[
                    "nfhl_yahoo_team_refresh_notice"
                ] = (
                    "Yahoo player refresh complete: "
                    f"{player_refresh_result['yahoo_player_count']} "
                    "players fetched; "
                    f"{player_refresh_result['db_player_count']} "
                    "player rows available. "
                    "Yahoo rankings, roster percentage, "
                    "eligibility, status, and draft analysis "
                    "were refreshed. Team metadata was "
                    "refreshed too."
                )

                st.cache_data.clear()
                st.session_state[
                    "nfhl_force_open_league_setup"
                ] = True
                st.rerun()

        st.caption(
            "Returning managers are mapped automatically. "
            "Manual controls appear only for current Yahoo "
            "managers who cannot be identified safely."
        )

        from draftboard.data.season_team_slots import (
            get_season_team_slots,
        )

        slots = (
            get_season_team_slots()
        )

        mapped = sum(
            1
            for row in slots
            if row.get(
                "current_team_key"
            )
        )

        pending = [
            row
            for row in slots
            if str(
                row.get(
                    "assignment_status"
                )
                or ""
            ).upper()
            == "PENDING"
        ]

        assigned_keys = {
            str(
                row.get(
                    "current_team_key"
                )
                or ""
            )
            for row in slots
            if row.get(
                "current_team_key"
            )
        }

        unmatched_current = [
            team
            for team in teams
            if str(
                team.get(
                    "team_key"
                )
                or ""
            )
            not in assigned_keys
        ]

        st.markdown(
            f"**Team mapping:** {mapped}/14"
        )

        if (
            pending
            and not unmatched_current
        ):
            st.info(
                "Waiting for Yahoo membership: "
                + ", ".join(
                    str(
                        row.get(
                            "prior_manager_name"
                        )
                        or "Unknown"
                    )
                    for row in pending
                )
                + ". No commissioner mapping action "
                  "is required right now."
            )

        st.divider()

        from draftboard.ui.components.season_team_slots import (
            render_season_team_slots,
        )

        render_season_team_slots(
            gateway_context=gateway_context,
            teams=teams,
        )


# NFHL_DRAFT_READINESS_UI_END


# NFHL_LIVE_DRAFT_UI_START
# ================================================================
# NFHL LIVE DRAFT
#
# NFFL-style UI:
# - fixed on-clock/picker dock
# - searchable player picker
# - graphical PostgreSQL board
# - expired/makeup pick access for managers
# ================================================================


def render_nfhl_draft_complete_banner(
    *,
    season_year: int,
    draft_status: str,
) -> None:
    """
    Render the terminal NFHL draft experience.

    PostgreSQL remains authoritative; this presentation is driven
    by its COMPLETE lifecycle state.
    """

    if str(
        draft_status
        or ""
    ).upper() != "COMPLETE":
        return

    st.markdown(
        "## 🏆 CONGRATULATIONS! 🏆"
    )

    st.markdown(
        f"### THE {season_year} NFHL DRAFT IS COMPLETE"
    )

    st.success(
        "The rosters are set and the chase for the championship "
        "begins. Good luck this season—may your stars stay healthy, "
        "your goalies stand tall, and your waiver claims clear! 🏒"
    )


def render_live_draft(
    *,
    gateway_context: dict[str, object],
    players: list[dict],
    status_only: bool = False,
) -> None:
    from draftboard.ui.components.live_draft import (
        render_live_draft_experience,
    )

    render_live_draft_experience(
        gateway_context=gateway_context,
        players=players,
        status_only=status_only,
    )


# NFHL_LIVE_DRAFT_UI_END


def render_draft_board(
    current_teams: int,
    target_teams: int,
    order_mode_status: str,
    *,
    gateway_context: dict[str, object],
    teams: list[dict],
    players: list[dict],
    draft_status: str,
) -> None:
    if current_teams != target_teams:
        st.info(
            f"League formation in progress: "
            f"{current_teams}/{target_teams} teams."
        )

    if order_mode_status != "verified":
        st.warning(
            "Draft order mode has not yet been finalized."
        )

    # NFHL_ROLLCALL_PREVIEW_UI_START
    from draftboard.ui.components.season_team_slots import (
        render_preview_draft_board,
    )

    render_preview_draft_board()

    render_draft_lifecycle_panel(
        gateway_context=gateway_context,
        show_status=False,
        show_commissioner_controls=False,
    )

    render_live_draft(
        gateway_context=gateway_context,
        players=players,
    )



def main() -> None:
    st.set_page_config(
        page_title="NFHL DraftBoard",
        page_icon="🏒",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    apply_theme()

    gateway_context = render_team_gateway()

    config = load_config()

    league = config["league"]
    draft = config["draft"]

    season_year = get_season_year()
    prior_year = season_year - 1

    summary = load_summary()
    teams = load_teams()
    players = load_players()

    target_teams = int(
        league["manager_count_target"]
    )

    current_teams = int(
        summary["team_count"]
    )

    draft_status = str(
        summary["draft_status"]
        or "UNKNOWN"
    )

    render_banner(season_year)

    render_nfhl_draft_complete_banner(
        season_year=season_year,
        draft_status=draft_status,
    )

    if draft_status != "COMPLETE":
        render_autopick_panel(
            gateway_context=gateway_context,
            teams=teams,
            players=players,
            draft_status=draft_status,
        )

    commissioner_mode = (
        str(
            gateway_context.get(
                "role"
            )
            or ""
        ).lower()
        == "commissioner"
    )

    tab_names = [
        "Draft Board",
        "Available Players",
        "Teams",
        "Draft Lottery",
        "Pick Tracker",
        "Draft Statistics",
    ]

    if commissioner_mode:
        tab_names.append(
            "Commissioner"
        )

    selected_view = st.segmented_control(
        "NFHL View",
        options=tab_names,
        default="Draft Board",
        key="nfhl_main_view_final",
        label_visibility="collapsed",
    )

    if selected_view not in tab_names:
        selected_view = "Draft Board"

    # Keep Pick / Clock / Time visible across every ACTIVE
    # DraftBoard view. Only the Draft Board itself gets picker
    # controls and the graphical board.
    if (
        draft_status == "ACTIVE"
        and selected_view != "Draft Board"
    ):
        render_live_draft(
            gateway_context=gateway_context,
            players=players,
            status_only=True,
        )

    if selected_view == "Draft Board":
        render_draft_board(
            current_teams,
            target_teams,
            str(
                draft.get(
                    "order_mode_status",
                    "unverified",
                )
            ).lower(),
            gateway_context=gateway_context,
            teams=teams,
            players=players,
            draft_status=draft_status,
        )

    elif selected_view == "Available Players":
        render_players(
            players,
            prior_year,
        )

    elif selected_view == "Teams":
        render_teams(
            teams,
            target_teams,
            players,
            gateway_context=gateway_context,
        )

    elif selected_view == "Draft Lottery":
        render_draft_lottery(
            current_teams,
            target_teams,
            is_commissioner=commissioner_mode,
        )

    elif selected_view == "Pick Tracker":
        from draftboard.ui.components.pick_tracker import (
            render_pick_tracker,
        )

        render_pick_tracker(
            players=players,
            teams=teams,
        )

    elif selected_view == "Draft Statistics":
        from draftboard.ui.components.draft_statistics import (
            render_draft_statistics,
        )

        render_draft_statistics(
            players=players,
        )

    elif (
        selected_view == "Commissioner"
        and commissioner_mode
    ):
        from draftboard.ui.components.commissioner_checklist import (
            render_commissioner_checklist,
        )

        render_commissioner_checklist(
            gateway_context=gateway_context,
        )

        render_draft_readiness_panel(
            gateway_context=gateway_context,
            teams=teams,
        )

        with st.expander(
            "Manager Access",
            expanded=False,
        ):
            render_manager_links()
            st.divider()
            render_gateway_audit()

        from draftboard.ui.components.commissioner_draft_setup import (
            render_draft_setup_panel,
            render_danger_zone,
        )

        render_draft_setup_panel(
            gateway_context=gateway_context,
            current_teams=current_teams,
            target_teams=target_teams,
        )

        render_draft_lifecycle_panel(
            gateway_context=gateway_context,
            show_status=False,
            show_commissioner_controls=True,
        )

        from draftboard.ui.components.commissioner_recovery import (
            render_commissioner_recovery,
        )

        render_commissioner_recovery(
            gateway_context=gateway_context,
        )

        render_danger_zone(
            gateway_context=gateway_context,
        )
    with st.sidebar:
        st.header("NFHL")

        st.write(
            f"Yahoo League: `{get_league_key()}`"
        )

        st.write(
            f"Season: `{season_year}`"
        )

        st.write(
            f"Prior Stats: `{prior_year}`"
        )




if __name__ == "__main__":
    main()
