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
        220
        + (
            len(display_rows)
            * 82
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
) -> None:
    st.subheader("NFHL Teams")

    st.caption(
        "Current Yahoo roll-call membership. "
        "Only real current-season Yahoo teams are shown."
    )

    if not teams:
        st.info("No Yahoo teams are loaded.")
        return

    display = pd.DataFrame(teams)[
        [
            "team_id",
            "team_name",
            "owner_name",
        ]
    ].copy()

    display.columns = [
        "#",
        "Team",
        "Manager",
    ]

    st.dataframe(
        display,
        hide_index=True,
        use_container_width=True,
    )

    st.caption(
        f"{len(teams)} of {target_teams} teams enrolled."
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

    df = player_dataframe(players)

    if df.empty:
        st.warning("No current Yahoo players are loaded.")
        return

    row1, row2, row3, row4 = st.columns(
        [2.2, 1.35, 1.35, 1.35]
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

    filtered = df.copy()

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

    metric1, metric2, metric3, metric4 = st.columns(4)

    metric1.metric(
        "Matching Players",
        f"{len(filtered):,}",
    )

    metric2.metric(
        f"With {prior_year} Stats",
        f"{int(filtered['stats_season_year'].notna().sum()):,}",
    )

    metric3.metric(
        "Yahoo Ranked",
        f"{int(filtered['rank_value'].notna().sum()):,}",
    )

    metric4.metric(
        "Draft % Available",
        f"{int(filtered['percent_drafted'].notna().sum()):,}",
    )

    display = filtered[
        [
            "rank_value",
            "full_name",
            "nhl_team_abbr",
            "primary_position",
            "eligible_display",
            "status_display",

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
) -> None:
    st.subheader("Draft Lottery")

    commissioner_mode = (
        str(
            st.query_params.get(
                "commissioner",
                "0",
            )
        )
        == "1"
    )

    if commissioner_mode:
        st.caption(
            "Commissioner View — draft lottery controls enabled."
        )

    st.caption(
        "All NFHL teams receive equal odds. "
        "The complete random order is persisted before the first "
        "result is revealed, and results reveal from the final "
        "draft slot back to Pick 1."
    )

    readiness_col, odds_col = st.columns(2)

    readiness_col.metric(
        "League Readiness",
        f"{current_teams}/{target_teams}",
    )

    equal_odds = (
        (100.0 / target_teams)
        if target_teams > 0
        else 0.0
    )

    odds_col.metric(
        "Initial Chance Per Slot",
        f"{equal_odds:.2f}%",
    )

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
        st.success(
            f"League membership complete: "
            f"{current_teams}/{target_teams} teams."
        )

    lottery = load_lottery_state()

    # ============================================================
    # NO LOTTERY EXISTS YET
    # ============================================================

    if lottery is None:
        st.info(
            "No NFHL Draft Lottery has been initialized."
        )

        if commissioner_mode:
            st.markdown("#### Commissioner Controls")

            initialize_clicked = st.button(
                "Initialize Draft Lottery",
                disabled=not league_ready,
                type="primary",
                key="nfhl_lottery_initialize",
            )

            if not league_ready:
                st.caption(
                    "Initialization remains disabled until the "
                    f"league has exactly {target_teams} real teams."
                )

            if initialize_clicked:
                try:
                    initialize_lottery(
                        expected_team_count=target_teams,
                        actor="commissioner_link",
                    )
                except Exception as exc:
                    st.error(
                        f"Could not initialize lottery: {exc}"
                    )
                else:
                    load_lottery_state.clear()
                    st.rerun()

        else:
            st.caption(
                "Lottery administration is available only from "
                "the Commissioner View."
            )

        return

    # ============================================================
    # EXISTING LOTTERY
    # ============================================================

    run = lottery["run"]
    picks = lottery["picks"]

    status = str(
        run["status"]
    ).upper()

    configured_team_count = int(
        run["configured_team_count"]
    )

    revealed_count = int(
        lottery["revealed_count"]
    )

    status_col, reveal_col = st.columns(2)

    status_col.metric(
        "Lottery Status",
        status,
    )

    reveal_col.metric(
        "Revealed",
        f"{revealed_count}/{configured_team_count}",
    )

    rows = []

    hidden_slots = []

    for pick in picks:

        revealed = (
            pick["revealed_at_utc"]
            is not None
        )

        if not revealed:
            hidden_slots.append(
                int(pick["slot_number"])
            )

        rows.append(
            {
                "Draft Slot": pick["slot_number"],
                "Status": (
                    "REVEALED"
                    if revealed
                    else "HIDDEN"
                ),
                "Team": (
                    pick["team_name"]
                    if revealed
                    else "—"
                ),
                "Manager": (
                    pick["owner_name"]
                    if revealed
                    else "—"
                ),
            }
        )

    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
    )

    if status == "FINALIZED":
        st.success(
            "Lottery finalized. "
            "The persisted draft order is locked."
        )

    elif revealed_count == configured_team_count:
        st.info(
            "All positions have been revealed. "
            "Commissioner finalization is still required."
        )

    # ============================================================
    # COMMISSIONER WRITE CONTROLS
    # ============================================================

    if commissioner_mode:
        st.markdown("#### Commissioner Controls")

        # --------------------------------------------------------
        # REVEAL
        # --------------------------------------------------------

        if hidden_slots:
            next_slot = max(hidden_slots)

            st.caption(
                "The next reveal is fixed by the persisted "
                "lottery result. No rerandomization occurs."
            )

            if st.button(
                f"Reveal Pick {next_slot}",
                type="primary",
                key="nfhl_lottery_reveal_next",
            ):
                try:
                    reveal_next_lottery_slot(
                        actor="commissioner_link",
                    )
                except Exception as exc:
                    st.error(
                        f"Could not reveal Pick "
                        f"{next_slot}: {exc}"
                    )
                else:
                    load_lottery_state.clear()
                    st.rerun()

        # --------------------------------------------------------
        # FINALIZE
        # --------------------------------------------------------

        elif status != "FINALIZED":
            confirm_finalize = st.checkbox(
                "Confirm final lottery order",
                value=False,
                key="nfhl_lottery_confirm_finalize",
            )

            if st.button(
                "Finalize Draft Lottery",
                disabled=not confirm_finalize,
                type="primary",
                key="nfhl_lottery_finalize",
            ):
                try:
                    finalize_lottery(
                        actor="commissioner_link",
                    )
                except Exception as exc:
                    st.error(
                        f"Could not finalize lottery: {exc}"
                    )
                else:
                    load_lottery_state.clear()
                    st.rerun()

        # --------------------------------------------------------
        # VOID / RESET
        # --------------------------------------------------------

        with st.expander(
            "Commissioner Reset / Void Lottery",
            expanded=False,
        ):
            st.warning(
                "This preserves the existing lottery in history "
                "as VOID and permits a new lottery to be created. "
                "Use only when the lottery must be explicitly "
                "invalidated."
            )

            void_reason = st.text_input(
                "Reason for voiding lottery",
                key="nfhl_lottery_void_reason",
            )

            confirm_void = st.checkbox(
                "Confirm void/reset",
                value=False,
                key="nfhl_lottery_confirm_void",
            )

            void_ready = bool(
                confirm_void
                and str(void_reason).strip()
            )

            if st.button(
                "Void Current Lottery",
                disabled=not void_ready,
                key="nfhl_lottery_void",
            ):
                try:
                    void_lottery(
                        actor="commissioner_link",
                        reason=str(void_reason).strip(),
                    )
                except Exception as exc:
                    st.error(
                        f"Could not void lottery: {exc}"
                    )
                else:
                    load_lottery_state.clear()
                    st.rerun()

    else:
        st.caption(
            "Lottery results are read-only on the public DraftBoard."
        )

    st.caption(
        "Production draft-pick initialization remains a separate "
        "step after the lottery is finalized and draft order mode "
        "is confirmed."
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
        expanded=False,
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
    # STATUS
    # ------------------------------------------------------------

    s1, s2, s3 = st.columns(3)

    s1.metric(
        "Queue",
        f"{len(queue_rows)}/5",
    )

    s2.metric(
        "Auto-Pick",
        "ARMED"
        if enabled
        else "OFF",
    )

    if next_pick:
        next_pick_label = (
            f"R{next_pick['round_number']} "
            f"• {next_pick['pick_id']}"
        )
    else:
        next_pick_label = (
            "Not Initialized"
        )

    s3.metric(
        "Next Pick",
        next_pick_label,
    )

    # ------------------------------------------------------------
    # PLAYER OPTIONS
    # ------------------------------------------------------------

    player_lookup: dict[str, dict] = {}

    player_keys: list[str] = []

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

    current_by_rank = {
        int(row["queue_rank"]):
        str(
            row["yahoo_player_key"]
        )
        for row in queue_rows
    }

    # ------------------------------------------------------------
    # QUEUE EDITOR
    # ------------------------------------------------------------

    with st.form(
        key=(
            "nfhl_autopick_queue_"
            + selected_team_key
        )
    ):
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

        save_queue = (
            st.form_submit_button(
                "Save Queue",
                use_container_width=True,
            )
        )

    if save_queue:
        normalized = [
            player_key
            for player_key
            in selections
            if player_key
        ]

        if (
            len(normalized)
            != len(set(normalized))
        ):
            st.error(
                "A player can appear only once "
                "in the queue."
            )

        else:
            actor = (
                "commissioner_link"
                if role == "commissioner"
                else (
                    "manager:"
                    + selected_team_key
                )
            )

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

                st.success(
                    "Draft queue saved. "
                    "Auto-Pick is OFF until "
                    "you explicitly arm it."
                )

                st.rerun()

            except Exception as exc:
                st.error(
                    "Unable to save queue: "
                    f"{exc}"
                )

    # ------------------------------------------------------------
    # AUTO-PICK CONTROL
    # ------------------------------------------------------------

    st.markdown(
        "**Auto-Pick Control**"
    )

    if enabled:
        st.success(
            "Auto-Pick is armed for "
            f"`{armed_pick_id}`."
        )

        if st.button(
            "Disable Auto-Pick",
            key=(
                "nfhl_disable_autopick_"
                + selected_team_key
            ),
            use_container_width=True,
        ):
            actor = (
                "commissioner_link"
                if role == "commissioner"
                else (
                    "manager:"
                    + selected_team_key
                )
            )

            try:
                disable_autopick(
                    team_key=(
                        selected_team_key
                    ),
                    actor=actor,
                )

                st.success(
                    "Auto-Pick disabled."
                )

                st.rerun()

            except Exception as exc:
                st.error(
                    "Unable to disable Auto-Pick: "
                    f"{exc}"
                )

        return

    # PREP / SETUP state: queue works, arming does not.
    if str(
        draft_status
    ).upper() != "ACTIVE":
        st.info(
            "Your queue can be prepared now. "
            "Auto-Pick becomes available when "
            "the NFHL draft is ACTIVE."
        )
        return

    if next_pick is None:
        st.info(
            "No open draft pick exists for this team."
        )
        return

    if not queue_rows:
        st.info(
            "Add at least one player to the queue "
            "before enabling Auto-Pick."
        )
        return

    arm_label = (
        "Arm Auto-Pick for "
        f"Round {next_pick['round_number']} "
        f"({next_pick['pick_id']})"
    )

    if st.button(
        arm_label,
        key=(
            "nfhl_arm_autopick_"
            + selected_team_key
        ),
        use_container_width=True,
        type="primary",
    ):
        actor = (
            "commissioner_link"
            if role == "commissioner"
            else (
                "manager:"
                + selected_team_key
            )
        )

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

            st.success(
                "Auto-Pick armed for the "
                "team's next open pick."
            )

            st.rerun()

        except Exception as exc:
            st.error(
                "Unable to arm Auto-Pick: "
                f"{exc}"
            )


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

        standard_clock = (
            is_nfhl_standard_clock_configured()
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
        board_initialized
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
            "24:00:00",
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

    if role != "commissioner":
        return

    # ------------------------------------------------------------
    # COMMISSIONER CONTROLS
    # ------------------------------------------------------------

    with st.expander(
        "Commissioner Draft Controls",
        expanded=(
            status == "ACTIVE"
        ),
    ):

        st.markdown(
            "#### NFHL Slow Draft Clock"
        )

        st.write(
            "**Standard:** 24 hours per pick"
        )

        st.write(
            "**Reminders:** 12 hours, 6 hours, "
            "and 1 hour remaining"
        )

        st.write(
            "**Expiration:** the missed pick stays open, "
            "while the next manager immediately goes on the clock"
        )

        st.write(
            "**Weekends:** count toward the 24-hour clock"
        )

        if standard_clock:
            st.success(
                "Standard NFHL 24-hour clock configuration is active."
            )

        elif status == "PREP":

            st.warning(
                "The NFHL clock is not using the standard "
                "24-hour configuration."
            )

            if st.button(
                "Apply Standard 24-Hour Clock",
                type="primary",
                use_container_width=True,
                key="nfhl_apply_standard_clock",
            ):

                try:
                    apply_nfhl_standard_clock_config(
                        actor="commissioner_link",
                    )

                except Exception as exc:
                    st.error(
                        "Clock configuration failed: "
                        f"{exc}"
                    )

                else:
                    st.success(
                        "Standard NFHL clock applied."
                    )

                    st.cache_data.clear()
                    st.rerun()

        else:
            st.error(
                "The active draft is not using the "
                "standard 24-hour clock configuration."
            )

        st.divider()

        st.markdown(
            "#### Draft Lifecycle"
        )

        if not board_initialized:

            st.info(
                "The Draft Board is not initialized yet. "
                "Clock rules can be prepared now; Start Draft "
                "will become available after the league is full, "
                "the lottery is finalized, and the board is initialized."
            )

            return

        if status == "PREP":

            if not standard_clock:
                st.warning(
                    "Apply the standard 24-hour clock "
                    "before starting the draft."
                )
                return

            confirm_start = st.checkbox(
                "I confirm the Draft Board is correct "
                "and want to start the NFHL draft.",
                key="nfhl_confirm_start_draft",
            )

            if st.button(
                "Start NFHL Draft",
                type="primary",
                use_container_width=True,
                disabled=not confirm_start,
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
                    st.rerun()

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
                        st.rerun()

        with c2:
            if st.button(
                "Reset Current Pick to 24 Hours",
                use_container_width=True,
                key="nfhl_reset_current_pick_clock",
            ):

                try:
                    set_nfhl_current_pick_remaining(
                        remaining_seconds=86400,
                        actor="commissioner_link",
                    )

                except Exception as exc:
                    st.error(
                        "Current-pick clock reset failed: "
                        f"{exc}"
                    )

                else:
                    st.success(
                        "Current pick reset to a full 24 hours."
                    )

                    st.cache_data.clear()
                    st.rerun()

        st.divider()

        st.markdown(
            "#### Adjust Current Pick"
        )

        st.caption(
            "This changes only the current manager's remaining "
            "time. Future picks remain 24 hours. For a longer "
            "commissioner hold, pause the draft instead."
        )

        current_remaining = int(
            snapshot.get(
                "remaining_seconds"
            )
            or 86400
        )

        default_hours = max(
            1,
            min(
                24,
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
            max_value=24,
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
                st.rerun()


# NFHL_DRAFT_LIFECYCLE_UI_END


# NFHL_DRAFT_READINESS_UI_START
# ================================================================
# COMMISSIONER — DRAFT READINESS / INITIALIZATION
# ================================================================


def render_draft_readiness_panel(
    *,
    gateway_context: dict[str, object],
) -> None:

    role = str(
        gateway_context.get("role")
        or "public"
    ).strip().lower()

    if role != "commissioner":
        return

    try:
        readiness = (
            get_draft_initialization_readiness()
        )

    except Exception as exc:
        st.error(
            "Unable to evaluate NFHL draft readiness: "
            f"{exc}"
        )
        return

    ready = bool(
        readiness.get("ready")
    )

    with st.expander(
        "Commissioner Draft Readiness",
        expanded=not ready,
    ):

        # NFHL_YAHOO_TEAM_REFRESH_UI_START
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
                st.rerun()

        st.caption(
            "Commissioner only. Refreshes Yahoo league "
            "membership without reloading the player universe. "
            "Available only while the draft remains in PREP."
        )
        # NFHL_YAHOO_TEAM_REFRESH_UI_END

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "Teams",
            (
                f"{readiness['live_team_count']}"
                f"/{readiness['manager_count']}"
            ),
        )

        c2.metric(
            "Draft Format",
            (
                str(
                    readiness.get(
                        "draft_order_mode"
                    )
                    or "Not Set"
                ).title()
            ),
        )

        c3.metric(
            "Lottery",
            (
                "Finalized"
                if (
                    readiness.get(
                        "finalized_lottery_count"
                    )
                    == 1
                )
                else "Not Finalized"
            ),
        )

        c4.metric(
            "Board",
            (
                "Initialized"
                if readiness.get(
                    "draft_pick_count",
                    0,
                )
                else "Not Initialized"
            ),
        )

        if ready:
            st.success(
                "NFHL is ready to initialize the "
                "production draft board."
            )

        else:
            st.info(
                "Draft initialization is locked until "
                "all prerequisites are satisfied."
            )

            blockers = (
                readiness.get("blockers")
                or []
            )

            for blocker in blockers:
                st.markdown(
                    f"- {blocker}"
                )

        st.caption(
            "Initialization creates the full "
            f"{readiness['expected_draft_pick_count']}-pick "
            "draft grid and sets the first pick. "
            "It does not start the draft clock."
        )

        confirm = st.checkbox(
            "I confirm the finalized lottery and "
            "draft format are correct and want to "
            "initialize the production NFHL Draft Board.",
            disabled=not ready,
            key="nfhl_confirm_initialize_draft",
        )

        if st.button(
            "Initialize NFHL Draft Board",
            type="primary",
            use_container_width=True,
            disabled=(
                not ready
                or not confirm
            ),
            key="nfhl_initialize_draft_board",
        ):

            try:
                result = (
                    initialize_draft_from_lottery(
                        actor="commissioner_link",
                    )
                )

            except Exception as exc:
                st.error(
                    "Draft initialization failed: "
                    f"{exc}"
                )
                return

            if (
                str(
                    result.get(
                        "result_status"
                    )
                    or ""
                ).upper()
                != "INITIALIZED"
            ):
                st.error(
                    "Draft initializer returned an "
                    "unexpected result."
                )
                return

            st.success(
                "NFHL Draft Board initialized: "
                f"{result['draft_pick_count']} picks, "
                f"first pick {result['first_pick_id']}."
            )

            st.cache_data.clear()
            st.rerun()


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


def render_live_draft(
    *,
    gateway_context: dict[str, object],
    players: list[dict],
) -> None:
    from draftboard.ui.components.live_draft import (
        render_live_draft_experience,
    )

    render_live_draft_experience(
        gateway_context=gateway_context,
        players=players,
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
    st.subheader("Draft Board")

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

    render_draft_readiness_panel(
        gateway_context=gateway_context,
    )

    render_draft_lifecycle_panel(
        gateway_context=gateway_context,
    )

    render_live_draft(
        gateway_context=gateway_context,
        players=players,
    )

    render_autopick_panel(
        gateway_context=gateway_context,
        teams=teams,
        players=players,
        draft_status=draft_status,
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

    player_count = int(
        summary["player_count"]
    )

    draft_status = str(
        summary["draft_status"]
        or "UNKNOWN"
    )

    render_banner(season_year)

    if draft_status.upper() == "PREP":
        st.info(
            "PREP MODE — Roll call is in progress. "
            "Player research is available now."
        )

    m1, m2, m3, m4, m5 = st.columns(5)

    m1.metric(
        "Teams",
        f"{current_teams}/{target_teams}",
    )

    m2.metric(
        "Current Players",
        f"{player_count:,}",
    )

    m3.metric(
        "Rounds",
        draft["rounds_total"],
    )

    m4.metric(
        "Season",
        season_year,
    )

    m5.metric(
        "Status",
        draft_status,
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

    tabs = st.tabs(
        tab_names
    )

    (
        board_tab,
        players_tab,
        teams_tab,
        lottery_tab,
    ) = tabs[:4]

    pick_tracker_tab = tabs[4]
    draft_statistics_tab = tabs[5]

    commissioner_tab = (
        tabs[6]
        if commissioner_mode
        else None
    )

    with board_tab:
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

    with players_tab:
        render_players(
            players,
            prior_year,
        )

    with teams_tab:
        render_teams(
            teams,
            target_teams,
        )

    with lottery_tab:
        render_draft_lottery(
            current_teams,
            target_teams,
        )

    with pick_tracker_tab:
        from draftboard.ui.components.pick_tracker import (
            render_pick_tracker,
        )

        render_pick_tracker(
            players=players,
        )

    with draft_statistics_tab:
        from draftboard.ui.components.draft_statistics import (
            render_draft_statistics,
        )

        render_draft_statistics(
            players=players,
        )

    if commissioner_tab is not None:
        with commissioner_tab:
            from draftboard.ui.components.season_team_slots import (
                render_season_team_slots,
            )

            render_season_team_slots(
                gateway_context=gateway_context,
                teams=teams,
            )


            # NFHL_MANAGER_LINKS_EXPANDER_START


            with st.expander(


                "Manager Links",


                expanded=False,


            ):


                render_manager_links()


            # NFHL_MANAGER_LINKS_EXPANDER_END
            from draftboard.ui.components.commissioner_recovery import (

                render_commissioner_recovery,

            )


            render_commissioner_recovery(

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

        if commissioner_mode:
            if st.button(
                "Refresh Data",
                use_container_width=True,
                key="nfhl_commissioner_refresh_data",
            ):
                st.cache_data.clear()
                st.rerun()


if __name__ == "__main__":
    main()
