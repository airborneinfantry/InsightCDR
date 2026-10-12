import os
from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from itsdangerous import URLSafeTimedSerializer
from app.auth import verify_password, create_session, read_session
from app.config import load_config
from app.db import get_conn

cfg = load_config(dict(os.environ))
serializer = URLSafeTimedSerializer(cfg["secret_key"])
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")


@app.on_event("startup")
def _seed_admin():
    from app.auth import seed_admin
    try:
        seed_admin(cfg)
    except Exception as e:
        print(f"seed_admin: {e}", flush=True)


def require(roles: list[str]):
    def dep(request: Request):
        token = request.cookies.get("ic_session")
        claim = read_session(serializer, token, cfg["session_ttl"]) if token else None
        if not claim:
            if request.headers.get("HX-Request"):
                # HTMX swaps the response body into the page: a bare Location
                # header would render the login page into the fragment. Send
                # the client-side redirect header instead (M-10a).
                raise HTTPException(status_code=302, headers={"HX-Redirect": "/login"})
            raise HTTPException(status_code=302, headers={"Location": "/login"})
        if claim.get("r") not in roles:
            raise HTTPException(status_code=403, detail="Forbidden: insufficient role")
        return request, claim["u"]
    return dep


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    conn = get_conn(cfg)
    row = conn.execute("SELECT password_hash, role FROM users WHERE username=%s", (username,)).fetchone()
    conn.close()
    if not row or not verify_password(password, row[0]):
        return templates.TemplateResponse(request, "login.html", {"error": "Invalid credentials"}, status_code=401)
    resp = RedirectResponse("/", status_code=303)
    # secure= follows the request scheme (https in prod via Caddy; http under
    # TestClient), and the cookie lifetime matches the configured session TTL.
    resp.set_cookie("ic_session", create_session(serializer, username, row[1]),
                    httponly=True, samesite="lax",
                    secure=(request.url.scheme == "https"),
                    max_age=cfg["session_ttl"])
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("ic_session")
    return resp


@app.get("/")
def home(request=Depends(require(["admin", "analyst", "viewer"]))):
    return RedirectResponse("/dashboard", status_code=303)


from app.routes.dashboard import router as dashboard_router  # noqa: E402
app.include_router(dashboard_router)
from app.routes.search import router as search_router  # noqa: E402
app.include_router(search_router)
from app.routes.quality import router as quality_router  # noqa: E402
app.include_router(quality_router)
from app.routes.utilization import router as utilization_router  # noqa: E402
app.include_router(utilization_router)
from app.routes.reports import router as reports_router  # noqa: E402
app.include_router(reports_router)
