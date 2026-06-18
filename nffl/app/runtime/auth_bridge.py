from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import os
import psycopg

HOST = "0.0.0.0"
PORT = 8601
COOKIE_NAME = str(os.environ.get("AUTH_COOKIE_NAME", "mlf_auth") or "mlf_auth")

def _dsn() -> str:
    return str(
        os.environ.get("POSTGRES_DSN")
        or os.environ.get("MLF_POSTGRES_DSN", "")
        or ""
    )

def _cookie_set_header(token: str, *, max_age: int = 30 * 24 * 60 * 60) -> str:
    return f"{COOKIE_NAME}={token}; Path=/; Max-Age={max_age}; SameSite=Strict; Secure; HttpOnly"

def _cookie_clear_header() -> str:
    return f"{COOKIE_NAME}=; Path=/; Max-Age=0; SameSite=Strict; Secure; HttpOnly"

def _redeem_handoff_code(*, handoff_code: str) -> str | None:
    dsn = _dsn()
    if not dsn:
        return None

    code = str(handoff_code or "").strip()
    if not code:
        return None

    sql = """
        update public.auth_handoff_code h
           set consumed_at_utc = now()
         where h.handoff_code = %s
           and h.consumed_at_utc is null
           and h.expires_at_utc > now()
        returning h.session_token
    """

    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (code,))
                row = cur.fetchone()
            conn.commit()
        return str(row[0]) if row and row[0] else None
    except Exception:
        return None

class Handler(BaseHTTPRequestHandler):
    def _handle(self, *, send_body: bool) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        next_url = qs.get("next", ["/"])[0] or "/"

        if parsed.path == "/auth/set":
            code = qs.get("code", [""])[0]
            session_token = _redeem_handoff_code(handoff_code=code)
            if not session_token:
                self.send_response(400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                if send_body:
                    self.wfile.write(b"invalid or expired code")
                return

            self.send_response(302)
            self.send_header("Location", next_url)
            self.send_header("Set-Cookie", _cookie_set_header(session_token))
            self.end_headers()
            return

        if parsed.path == "/auth/clear":
            self.send_response(302)
            self.send_header("Location", next_url)
            self.send_header("Set-Cookie", _cookie_clear_header())
            self.end_headers()
            return

        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        if send_body:
            self.wfile.write(b"not found")

    def do_GET(self):
        self._handle(send_body=True)

    def do_HEAD(self):
        self._handle(send_body=False)

    def log_message(self, format, *args):
        return

if __name__ == "__main__":
    HTTPServer((HOST, PORT), Handler).serve_forever()
