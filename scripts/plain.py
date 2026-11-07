#!/usr/bin/env python3
"""Plain-language explanations for statuses and failures, so a person does not have to decode exit codes.

Every function returns {"plain": one sentence a non-expert understands, "next": what to do (may be empty)}.
The raw technical value (exit code, log tail, status word) is always kept next to it by the caller; this only adds the sentence.
"""

REPLAY = {
    "confirmed": ("The problem happened every time the check was repeated, so it is real.", ""),
    "flaky": ("The problem happens only some of the time (a timing issue). It is real but intermittent.", "A verifier will judge it."),
    "not_reproducible": ("The check was repeated and the problem did not appear, so it cannot be proven.", "Kept only if several agents saw it."),
    "error": ("The tester's own check script is broken (it crashed or could not start), so it proves nothing about your app.", "Ignore this finding or re-run the tester."),
    "tamper": ("The tester changed your system under test while checking it. Its changes were undone and its findings set aside.", "Review the agent's log."),
    "none": ("This finding has not been re-run yet.", "Run /tusk-feedback."),
}
VERDICT = {
    "accept": ("Confirmed and queued as a real issue.", ""),
    "reject": ("A verifier looked at it and decided it is not a bug.", ""),
    "needs_verifier": ("Waiting for a second opinion: the replay could not settle it.", "A fresh verifier will decide."),
    "watch": ("Only one agent saw a timing problem and replay could not repeat it. Kept as a lead, not queued for fixing.", ""),
    "drop": ("Not reproducible and seen by only one agent, so it was set aside.", ""),
    "unreplayed": ("Not re-run yet.", "Run the replay step."),
}
RC = {
    1: "The agent program stopped with an error.",
    2: "The agent edited your system under test, so its work was set aside.",
    3: "The agent started but did not answer the test question, so it is not a usable model.",
    4: "Every model listed for this agent is out of quota, so nothing ran.",
    124: "The agent ran out of time and was stopped.",
    126: "The agent program could not be started (permissions).",
    127: "The agent program is not installed on this machine.",
    137: "The agent was killed (out of memory or stopped).",
}


def replay(status):
    p, n = REPLAY.get(status or "none", ("Unknown replay result.", ""))
    return {"plain": p, "next": n}


def verdict(v):
    p, n = VERDICT.get(v or "", ("", ""))
    return {"plain": p, "next": n}


def agent(status, rc=None, note="", tail="", tamper=False):
    """Why an agent ended the way it did."""
    if status in ("queued", "running"):
        return {"plain": "", "next": ""}
    if tamper:
        return {"plain": RC[2], "next": "Its findings are in .tusk/quarantine/."}
    if status == "done":
        return {"plain": "Finished normally.", "next": ""}
    low = (note + " " + tail).lower()
    if "quota" in low or "rate limit" in low or "429" in low or rc == 4:
        return {"plain": RC[4], "next": "Wait for the quota to reset or add another model to this agent's list."}
    if "bwrap:" in low:
        return {"plain": "The safety sandbox could not start on this machine, so the agent was not run.", "next": "Install bubblewrap, or check the sandbox settings."}
    if "downloading" in low and rc == 124:
        return {"plain": "The agent program was still being downloaded by the tool manager when time ran out. It never ran.", "next": "Run it once by hand so it installs, then try again."}
    if any(k in low for k in ("not logged in", "unauthorized", "401", "authentication required", "no auth type", "login flow", "log in", "login")):
        return {"plain": "The agent program is not logged in.", "next": "Log in to that agent CLI on the machine that runs it."}
    return {"plain": RC.get(rc, "The agent did not finish cleanly."), "next": "Open the technical details below."}


def lane(healthy):
    return {"plain": "Ready." if healthy else "This test copy of your app is not running right now.", "next": "" if healthy else "It is started automatically before each agent."}


def audit(ok, bad_seq):
    if ok:
        return {"plain": "No record has been changed or removed.", "next": ""}
    if bad_seq == 0:
        return {"plain": "The off-project copy of the log's latest position is missing, so the log cannot be proven complete.", "next": "If you wiped .tusk on purpose: python3 scripts/audit.py reset"}
    return {"plain": "A record was edited, removed or cut off after it was written.", "next": "Treat results after that record with caution."}


def kb_status(s):
    return {
        "open": "Found, nobody is fixing it yet.", "approved": "You approved a fix; it has not started.", "fixing": "A fix is being written.",
        "fixed": "A fix passed its check; the next run will test it again.", "verified": "Fixed and it passed again in a later run.",
        "regressed": "It came back after being fixed.", "wontfix": "Rejected, will not be fixed.", "not_seen": "Not found in the latest run.",
    }.get(s, "")
