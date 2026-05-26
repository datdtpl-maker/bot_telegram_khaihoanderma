import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow


OUT_DIR = Path(__file__).resolve().parent
ENV_FILE = OUT_DIR / "telegram_bot.env"
SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
]


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env_path(name: str, default: str) -> Path:
    value = os.environ.get(name, default).strip()
    path = Path(value)
    if not path.is_absolute():
        path = OUT_DIR / path
    return path


def main() -> None:
    load_env(ENV_FILE)
    client_secret_path = env_path("GOOGLE_OAUTH_CLIENT_SECRET_JSON", "google-oauth-client.json")
    token_path = env_path("GOOGLE_OAUTH_TOKEN_JSON", "google-oauth-token.json")

    if not client_secret_path.exists():
        raise SystemExit(
            f"Khong tim thay file OAuth client: {client_secret_path}\n"
            "Hay tai file OAuth Client JSON tu Google Cloud va dat vao thu muc bot."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    credentials = flow.run_local_server(port=0, prompt="consent")
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    print(f"Da tao token OAuth: {token_path}")


if __name__ == "__main__":
    main()
