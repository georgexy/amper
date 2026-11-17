"""Copia de siguranță legată din aplicație (backup_link.py): rclone authorize simulat."""
import os
import queue
import stat
import types

import pytest

from amper.backup_link import BackupLink, LinkError

AUTH = "http://127.0.0.1:53682/auth?state=abc123"
GOOGLE = "https://accounts.google.com/o/oauth2/auth?client_id=x&state=abc123"
TOKEN = '{"access_token":"ya29.x","token_type":"Bearer","refresh_token":"1//r","expiry":"2026-11-17T20:00:00Z"}'


class FakeProc:
    """rclone authorize: afișează linkul local, apoi (după ce Pi-ul trimite codul) cheia."""
    def __init__(self, give_url=True):
        self.q = queue.Queue()
        self.code = None
        if give_url:
            self.q.put(f"If your browser doesn't open automatically go to the following link: {AUTH}\n")
            self.q.put("Log in and authorize rclone for access\nWaiting for code...\n")
        else:
            self.q.put("Error: failed to start auth webserver: listen tcp 127.0.0.1:53682: address already in use\n")
            self.q.put(None)
        self.stdout = iter(self.q.get, None)
        self.ended = not give_url

    def deliver(self, ok=True):
        if ok:
            self.q.put(f"Got code\nPaste the following into your remote machine --->\n{TOKEN}\n<---End paste\n")
        else:
            self.q.put("Error: oauth2: \"invalid_grant\"\n")
        self.q.put(None)
        self.ended = True

    def poll(self):
        return 0 if self.ended else None

    def terminate(self):
        self.ended = True
        self.q.put(None)

    def wait(self, timeout=None):
        return 0


def _link(tmp_path, proc, token_ok=True, mkdir_rc=0):
    calls = []

    def run(*a, timeout=60):
        calls.append(a)
        rc = mkdir_rc if "mkdir" in a else 0
        out = "enabled" if a[:2] == ("systemctl", "is-enabled") else ""
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr="" if rc == 0 else "Failed to mkdir: 403 Forbidden")

    def fetch(url, follow=True):
        if url == AUTH and not follow:
            return GOOGLE
        if url.startswith("http://127.0.0.1:53682/?") and follow:
            proc.deliver(token_ok)
            return "<html>Success</html>"
        raise AssertionError(url)
    b = BackupLink(etc_dir=str(tmp_path / "etc"), log_dir=str(tmp_path / "log"), run=run, popen=lambda *a: proc,
                   fetch=fetch)
    return b, calls


def test_full_link_writes_conf_and_starts_timer(tmp_path):
    proc = FakeProc()
    b, calls = _link(tmp_path, proc)
    assert not b.status()["linked"]
    job = b.start()
    assert job["state"] == "waiting" and job["url"] == GOOGLE
    st = b.finish("http://127.0.0.1:53682/?state=abc123&code=4/0AbCd&scope=https://www.googleapis.com/auth/drive.file")
    assert st["linked"] and st["job"]["state"] == "done"
    conf = tmp_path / "etc" / "rclone.conf"
    text = conf.read_text()
    assert "[gdrive]" in text and "scope = drive.file" in text and '"refresh_token":"1//r"' in text
    assert stat.S_IMODE(os.stat(conf).st_mode) == 0o600
    assert ("systemctl", "enable", "--now", "amper-backup.timer") in calls
    assert any("mkdir" in c and "gdrive:back-up" in c for c in calls)


def test_wrong_pasted_address_is_refused(tmp_path):
    proc = FakeProc()
    b, _ = _link(tmp_path, proc)
    b.start()
    for bad in ("https://accounts.google.com/?code=1&state=2", "http://127.0.0.1:53682/?state=abc", "salut"):
        with pytest.raises(LinkError, match="nu e cea bună"):
            b.finish(bad)
    assert not (tmp_path / "etc" / "rclone.conf").exists()


def test_rclone_cannot_start(tmp_path):
    b, _ = _link(tmp_path, FakeProc(give_url=False))
    with pytest.raises(LinkError, match="address already in use"):
        b.start()


def test_google_refuses(tmp_path):
    proc = FakeProc()
    b, _ = _link(tmp_path, proc, token_ok=False)
    b.start()
    with pytest.raises(LinkError, match="invalid_grant"):
        b.finish("http://127.0.0.1:53682/?state=abc123&code=x")
    assert not b.linked()


def test_unlink_keeps_old_key_and_stops_timer(tmp_path):
    proc = FakeProc()
    b, calls = _link(tmp_path, proc)
    b.start()
    b.finish("http://127.0.0.1:53682/?state=abc123&code=x")
    st = b.unlink()
    assert not st["linked"] and (tmp_path / "etc" / "rclone.conf.old").exists()
    assert ("systemctl", "disable", "--now", "amper-backup.timer") in calls


def test_last_run_from_log(tmp_path):
    b, _ = _link(tmp_path, FakeProc())
    log = tmp_path / "log"
    log.mkdir()
    (log / "backup.log").write_text(
        "2026-11-16 00:00:01 ===== copia de siguranță (tot) =====\n2026-11-16 00:00:20 gata\n"
        "2026-11-17 00:00:01 ===== copia de siguranță (tot) =====\n"
        "2026-11-17 00:17:00 EȘUAT — clona: încărcarea în Drive a eșuat: no such host\n")
    last = b.last_run()
    assert last == {"at": "2026-11-17 00:00:01", "what": "tot", "state": "failed", "end": "2026-11-17 00:17:00",
                    "why": "clona: încărcarea în Drive a eșuat: no such host"}
