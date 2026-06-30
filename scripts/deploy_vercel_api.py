#!/usr/bin/env python3
"""Deploy to Vercel via REST API (env sync + file upload)."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
AUTH_PATH = Path.home() / "Library/Application Support/com.vercel.cli/auth.json"
PROJECT_JSON = ROOT / ".vercel/project.json"
VERCELIGNORE = ROOT / ".vercelignore"
PROJECT_NAME = "read-ai-claude-telegram"

API = "https://api.vercel.com"


def load_token() -> str:
    data = json.loads(AUTH_PATH.read_text())
    return str(data["token"])


def load_project() -> tuple[str, str]:
    if PROJECT_JSON.exists():
        data = json.loads(PROJECT_JSON.read_text())
        return str(data["projectId"]), str(data["orgId"])
    return "", ""


def resolve_team_id(token: str) -> str:
    response = httpx.get(
        f"{API}/v2/teams",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    response.raise_for_status()
    teams = response.json().get("teams", [])
    if not teams:
        user = httpx.get(
            f"{API}/v2/user",
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        ).json()
        return str(user.get("user", {}).get("id", ""))
    return str(teams[0]["id"])


def ensure_project(token: str, team_id: str) -> str:
    headers = {"Authorization": f"Bearer {token}"}
    params = {"teamId": team_id} if team_id.startswith("team_") else None

    project_id, linked_team = load_project()
    if project_id:
        probe = httpx.get(
            f"{API}/v9/projects/{project_id}",
            headers=headers,
            params={"teamId": linked_team} if linked_team.startswith("team_") else None,
            timeout=60,
        )
        if probe.status_code == 200:
            return project_id

    by_name = httpx.get(
        f"{API}/v9/projects/{PROJECT_NAME}",
        headers=headers,
        params=params,
        timeout=60,
    )
    if by_name.status_code == 200:
        project_id = str(by_name.json()["id"])
    else:
        created = httpx.post(
            f"{API}/v11/projects",
            headers=headers,
            params=params,
            json={"name": PROJECT_NAME, "framework": None},
            timeout=60,
        )
        created.raise_for_status()
        project_id = str(created.json()["id"])
        print(f"created project: {project_id}")

    PROJECT_JSON.parent.mkdir(exist_ok=True)
    PROJECT_JSON.write_text(
        json.dumps(
            {
                "projectId": project_id,
                "orgId": team_id,
                "projectName": PROJECT_NAME,
            },
            indent=2,
        )
        + "\n"
    )
    return project_id


def load_env_file() -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = ROOT / ".env"
    if not env_path.exists():
        return values
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    values["TELEGRAM_POLLING"] = "false"
    values["PROMPT_FILE"] = "prompts/tasks_extraction.txt"
    values["TELEGRAM_WEBHOOK_URL"] = (
        "https://read-ai-claude-telegram.vercel.app/webhooks/telegram"
    )
    return values


def ignored(path: Path, patterns: list[str]) -> bool:
    rel = path.as_posix()
    for pattern in patterns:
        pattern = pattern.strip()
        if not pattern:
            continue
        if pattern.endswith("/"):
            if rel.startswith(pattern.rstrip("/")) or f"/{pattern.rstrip('/')}/" in f"/{rel}/":
                return True
        elif "*" in pattern:
            import fnmatch

            if fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(rel, pattern):
                return True
        elif rel == pattern or rel.endswith(f"/{pattern}"):
            return True
    return False


def collect_files() -> list[tuple[str, bytes]]:
    ignore_patterns = [
        line.strip()
        for line in VERCELIGNORE.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    files: list[tuple[str, bytes]] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if ignored(path, ignore_patterns):
            continue
        if rel.startswith(".vercel/"):
            continue
        files.append((rel, path.read_bytes()))
    return files


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def sync_env(token: str, project_id: str, team_id: str, env: dict[str, str]) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    params = {"teamId": team_id}
    existing = httpx.get(
        f"{API}/v9/projects/{project_id}/env",
        headers=headers,
        params=params,
        timeout=60,
    )
    existing.raise_for_status()
    by_key = {item["key"]: item for item in existing.json().get("envs", [])}

    skip = {"PORT", "DATABASE_PATH", "TELEGRAM_CHAT_ID"}
    for key, value in env.items():
        if key in skip:
            continue
        if not value:
            continue

        if key in by_key:
            env_id = by_key[key]["id"]
            httpx.delete(
                f"{API}/v9/projects/{project_id}/env/{env_id}",
                headers=headers,
                params=params,
                timeout=60,
            ).raise_for_status()

        response = httpx.post(
            f"{API}/v10/projects/{project_id}/env",
            headers=headers,
            params=params,
            json={
                "key": key,
                "value": value,
                "type": "encrypted",
                "target": ["production", "preview", "development"],
            },
            timeout=60,
        )
        response.raise_for_status()
        print(f"env: {key}")


def upload_files(token: str, team_id: str, files: list[tuple[str, bytes]]) -> list[dict]:
    headers_base = {"Authorization": f"Bearer {token}"}
    uploaded: list[dict] = []
    for rel, content in files:
        digest = sha1(content)
        response = httpx.post(
            f"{API}/v2/files",
            headers={
                **headers_base,
                "x-vercel-digest": digest,
                "Content-Length": str(len(content)),
            },
            params={"teamId": team_id},
            content=content,
            timeout=120,
        )
        response.raise_for_status()
        uploaded.append({"file": rel, "sha": digest, "size": len(content)})
        print(f"file: {rel}")
    return uploaded


def create_deployment(
    token: str,
    project_id: str,
    team_id: str,
    project_name: str,
    files: list[dict],
) -> str:
    response = httpx.post(
        f"{API}/v13/deployments",
        headers={"Authorization": f"Bearer {token}"},
        params={"teamId": team_id},
        json={
            "name": project_name,
            "project": project_id,
            "target": "production",
            "files": files,
        },
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    return str(data.get("url") or data.get("alias", [""])[0])


def wait_ready(token: str, team_id: str, deployment_url: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    params = {"teamId": team_id}
    for _ in range(90):
        response = httpx.get(
            f"{API}/v13/deployments/{deployment_url}",
            headers=headers,
            params=params,
            timeout=60,
        )
        response.raise_for_status()
        state = response.json().get("readyState")
        print(f"state: {state}")
        if state == "READY":
            return
        if state in {"ERROR", "CANCELED"}:
            raise RuntimeError(f"Deployment failed: {state}")
        time.sleep(5)
    raise RuntimeError("Deployment timeout")


def disable_deployment_protection(token: str, project_id: str, team_id: str) -> None:
    response = httpx.patch(
        f"{API}/v9/projects/{project_id}",
        headers={"Authorization": f"Bearer {token}"},
        params={"teamId": team_id},
        json={"ssoProtection": None},
        timeout=60,
    )
    response.raise_for_status()
    print("deployment protection: disabled")


def main() -> None:
    token = load_token()
    team_id = resolve_team_id(token)
    project_id = ensure_project(token, team_id)
    print(f"team: {team_id}")
    print(f"project: {project_id}")

    print("Syncing env...")
    sync_env(token, project_id, team_id, load_env_file())

    print("Collecting files...")
    raw_files = collect_files()
    print(f"Uploading {len(raw_files)} files...")
    file_refs = upload_files(token, team_id, raw_files)

    print("Creating deployment...")
    url = create_deployment(token, project_id, team_id, PROJECT_NAME, file_refs)
    print(f"deployment: {url}")
    wait_ready(token, team_id, url)
    disable_deployment_protection(token, project_id, team_id)
    print("READY:", f"https://{url}" if not url.startswith("http") else url)
    print("PRODUCTION:", "https://read-ai-claude-telegram.vercel.app")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
