#!/usr/bin/env python3

"""
Sends updates to a Discord webhook for new changelog entries since the last GitHub Actions publish run.

Automatically figures out the last run and changelog contents with the GitHub API.
"""

import os
import time
from pathlib import Path
from typing import Any, Iterable

import requests
import yaml

DEBUG = False
DEBUG_CHANGELOG_FILE_OLD = Path("Resources/Changelog/Old.yml")
GITHUB_API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")

# https://discord.com/developers/docs/resources/webhook
DISCORD_EMBEDS_PER_MESSAGE_LIMIT = 10
DISCORD_EMBEDS_TOTAL_TEXT_LIMIT = 5900
DISCORD_EMBED_DESCRIPTION_LIMIT = 3900
MAX_CHANGE_MESSAGE_LENGTH = 1000
ERIDA_EMBED_COLOR = 0xA06DA8

CHANGELOG_FILE = "Resources/Changelog/Erida.yml" # Erida edit

TYPES_TO_EMOJI = {"Fix": "🐛", "Add": "🆕", "Remove": "❌", "Tweak": "⚒️"}

ChangelogEntry = dict[str, Any]


def main():
    webhook_erida = os.environ.get("DISCORD_WEBHOOK_URL_ERIDA")

    if not webhook_erida:
        raise RuntimeError("DISCORD_WEBHOOK_URL_ERIDA is not set")

    if DEBUG:
        # to debug this script locally, you can use
        # a separate local file as the old changelog
        last_changelog_stream = DEBUG_CHANGELOG_FILE_OLD.read_text()
    else:
        # when running this normally in a GitHub actions workflow,
        # it will get the old changelog from the GitHub API
        last_changelog_stream = get_last_changelog()

    last_changelog = yaml.safe_load(last_changelog_stream)
    with open(CHANGELOG_FILE, "r") as f:
        cur_changelog = yaml.safe_load(f)

    diff = list(diff_changelog(last_changelog, cur_changelog))

    if not diff:
        print("No new changelog entries since the last successful publish")
        return

    print(f"Sending {len(diff)} changelog entries since the last successful publish")
    send_embeds(changelog_entries_to_embeds(diff))


def get_most_recent_workflow(
    sess: requests.Session, github_repository: str, github_run: str
) -> Any:
    workflow_run = get_current_run(sess, github_repository, github_run)
    past_runs = get_past_runs(sess, workflow_run)
    for run in past_runs["workflow_runs"]:
        # First past successful run that isn't our current run.
        if run["id"] == workflow_run["id"]:
            continue

        return run

    raise RuntimeError("No previous successful publish workflow run found")


def get_current_run(
    sess: requests.Session, github_repository: str, github_run: str
) -> Any:
    resp = sess.get(
        f"{GITHUB_API_URL}/repos/{github_repository}/actions/runs/{github_run}"
    )
    resp.raise_for_status()
    return resp.json()


def get_past_runs(sess: requests.Session, current_run: Any) -> Any:
    """
    Get all successful workflow runs before our current one.
    """
    params = {"status": "success", "created": f"<={current_run['created_at']}"}
    resp = sess.get(f"{current_run['workflow_url']}/runs", params=params)
    resp.raise_for_status()
    return resp.json()


def get_last_changelog() -> str:
    github_repository = os.environ["GITHUB_REPOSITORY"]
    github_run = os.environ["GITHUB_RUN_ID"]
    github_token = os.environ["GITHUB_TOKEN"]

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {github_token}"
    session.headers["Accept"] = "application/vnd.github+json"
    session.headers["X-GitHub-Api-Version"] = "2022-11-28"

    most_recent = get_most_recent_workflow(session, github_repository, github_run)
    last_sha = most_recent["head_sha"]
    print(f"Last successful publish job was {most_recent['id']}: {last_sha}")
    last_changelog_stream = get_last_changelog_by_sha(
        session, last_sha, github_repository
    )

    return last_changelog_stream


def get_last_changelog_by_sha(
    sess: requests.Session, sha: str, github_repository: str
) -> str:
    """
    Use GitHub API to get the previous version of the changelog YAML (Actions builds are fetched with a shallow clone)
    """
    params = {
        "ref": sha,
    }
    headers = {"Accept": "application/vnd.github.raw"}

    resp = sess.get(
        f"{GITHUB_API_URL}/repos/{github_repository}/contents/{CHANGELOG_FILE}",
        headers=headers,
        params=params,
    )
    resp.raise_for_status()
    return resp.text


def diff_changelog(
    old: dict[str, Any], cur: dict[str, Any]
) -> Iterable[ChangelogEntry]:
    """
    Find all new entries not present in the previous publish.
    """
    old_entry_ids = {e["id"] for e in old.get("Entries", [])}
    return (e for e in cur.get("Entries", []) if e["id"] not in old_entry_ids)


def get_discord_body(embeds: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "embeds": embeds,
        "allowed_mentions": {"parse": []},
        "flags": 0,
    }


def send_with_retry(webhook_url: str, body: dict[str, Any], name: str) -> None:
    retry_attempt = 0
    MAX_RETRIES = 20

    while True:
        try:
            response = requests.post(webhook_url, json=body, timeout=10)
            if response.status_code == 429:
                retry_attempt += 1
                if retry_attempt > MAX_RETRIES:
                    raise RuntimeError(f"[{name}] Too many retries, giving up")
                retry_after = response.json().get("retry_after", 5)
                print(f"[{name}] Rate limited, retrying after {retry_after} seconds")
                time.sleep(retry_after)
                continue

            response.raise_for_status()
            print(f"Sent to {name} webhook")
            break
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"[{name}] Failed to send message") from e


def send_discord_webhook(embeds: list[dict[str, Any]]) -> None:
    webhook_url_erida = os.environ.get("DISCORD_WEBHOOK_URL_ERIDA")

    if webhook_url_erida:
        send_with_retry(webhook_url_erida, get_discord_body(embeds), "Erida")

    if not webhook_url_erida:
        raise RuntimeError("No Discord webhooks configured!")


def changelog_entries_to_embeds(entries: Iterable[ChangelogEntry]) -> list[dict[str, Any]]:
    """Process structured changelog entries into Discord embeds."""
    return [entry_to_embed(entry) for entry in entries]


def entry_to_embed(entry: ChangelogEntry) -> dict[str, Any]:
    lines = []

    for change in entry["changes"]:
        emoji = TYPES_TO_EMOJI.get(change["type"], "❓")
        message = truncate(str(change["message"]), MAX_CHANGE_MESSAGE_LENGTH)
        lines.append(f"{emoji} - {message}")

    description = truncate("\n".join(lines), DISCORD_EMBED_DESCRIPTION_LIMIT)
    title = truncate(str(entry.get("title") or "Changelog update"), 256)

    embed: dict[str, Any] = {
        "title": title,
        "description": description,
        "color": ERIDA_EMBED_COLOR,
        "footer": {
            "text": truncate(str(entry.get("author") or "Unknown"), 256),
        },
    }

    url = entry.get("url")
    if url and str(url).strip():
        embed["url"] = str(url)

    avatar_url = entry.get("avatar_url")
    if avatar_url and str(avatar_url).strip():
        embed["footer"]["icon_url"] = str(avatar_url)

    timestamp = entry.get("time")
    if timestamp:
        embed["timestamp"] = str(timestamp)

    return embed


def truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value

    return value[: limit - 6].rstrip() + " [...]"


def embed_text_length(embed: dict[str, Any]) -> int:
    footer = embed.get("footer") or {}
    return (
        len(str(embed.get("title") or ""))
        + len(str(embed.get("description") or ""))
        + len(str(footer.get("text") or ""))
    )


def iter_embed_batches(embeds: list[dict[str, Any]]) -> Iterable[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    batch_length = 0

    for embed in embeds:
        embed_length = embed_text_length(embed)
        batch_full = len(batch) >= DISCORD_EMBEDS_PER_MESSAGE_LIMIT
        batch_too_long = batch_length + embed_length > DISCORD_EMBEDS_TOTAL_TEXT_LIMIT

        if batch and (batch_full or batch_too_long):
            yield batch
            batch = []
            batch_length = 0

        batch.append(embed)
        batch_length += embed_length

    if batch:
        yield batch


def send_embeds(embeds: list[dict[str, Any]]) -> None:
    for batch in iter_embed_batches(embeds):
        send_discord_webhook(batch)


if __name__ == "__main__":
    main()
