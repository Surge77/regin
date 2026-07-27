import pytest

from regin.permissions import AutoApprove, DenyAll, PermissionError_, Permissions


def test_allows_a_tool_marked_allow() -> None:
    Permissions({"read_file": "allow"}).check("read_file", "read_file(path=a.py)")


def test_denies_a_tool_marked_deny() -> None:
    with pytest.raises(PermissionError_):
        Permissions({"bash": "deny"}).check("bash", "bash(cmd=rm -rf /)")


def test_unknown_tool_defaults_to_ask_and_is_refused_without_a_prompter() -> None:
    with pytest.raises(PermissionError_):
        Permissions({}, prompter=DenyAll()).check("write_file", "write_file(path=a.py)")


def test_ask_is_granted_when_the_prompter_confirms() -> None:
    Permissions({"write_file": "ask"}, prompter=AutoApprove()).check("write_file", "summary")


def test_auto_approve_grants_every_ask() -> None:
    Permissions({"bash": "ask"}, auto_approve=True).check("bash", "bash(cmd=pytest)")


def test_auto_approve_does_not_override_an_explicit_deny() -> None:
    with pytest.raises(PermissionError_):
        Permissions({"bash": "deny"}, auto_approve=True).check("bash", "bash(cmd=pytest)")
