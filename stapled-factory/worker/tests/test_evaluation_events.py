import json
import os
from pathlib import Path
import sys
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation_events import audit_events


def fixture(tmp_path, events):
    cid=str(uuid.uuid4())
    folder=tmp_path/".git"/"factory-home"/"conversations"/cid.replace("-", "")/"events"
    folder.mkdir(parents=True)
    for i,event in enumerate(events):
        (folder/f"{i}.json").write_text(json.dumps(event))
    return cid,folder


def pair(code=0):
    return [
        {"id":"action","timestamp":"01","kind":"ActionEvent","tool_name":"terminal","tool_call_id":"call","action":{"command":"SECRET_COMMAND","thought":"SECRET_THOUGHT"}},
        {"id":"obs","timestamp":"02","kind":"ObservationEvent","tool_name":"terminal","tool_call_id":"call","action_id":"action","observation":{"exit_code":code,"content":[{"type":"text","text":"SECRET_OUTPUT"}]}}]


def test_reduces_paired_results_and_exact_diagnostic_without_exporting_text(tmp_path):
    events=pair(7)+[{"id":"message","timestamp":"03","kind":"MessageEvent","source":"user","llm_message":{"role":"user","content":[{"type":"text","text":"fixed diagnostic"}]}}]
    cid,folder=fixture(tmp_path,events)
    (folder/"state.lock").write_text("not json")
    result=audit_events(str(tmp_path),cid,"fixed diagnostic")
    assert result["full_completed"] is True
    assert result["diagnostic_messages"]==1
    assert result["terminal_completed"]==1
    assert result["terminal_failed"]==1
    assert result["terminal_exit_codes"]=={"7":1}
    assert result["truncations"] is None
    assert len(result["command_sha256"][0])==64
    assert "SECRET" not in json.dumps(result)


def test_unpaired_or_timeout_result_not_completed(tmp_path):
    events=pair(-1)
    events[1]["observation"]["timeout"]=True
    cid,_=fixture(tmp_path,events)
    result=audit_events(str(tmp_path),cid,"fixed")
    assert result["terminal_completed"]==0
    assert result["terminal_incomplete"]==1
    assert result["terminal_failed"]==0


@pytest.mark.parametrize("kind",["symlink","hardlink","directory"])
def test_refuses_unsafe_json_files(tmp_path,kind):
    cid,folder=fixture(tmp_path,pair())
    target=tmp_path/"outside";target.write_text("{}")
    path=folder/"unsafe.json"
    if kind=="symlink":path.symlink_to(target)
    elif kind=="hardlink":os.link(target,path)
    else:path.mkdir()
    with pytest.raises((ValueError,OSError)):
        audit_events(str(tmp_path),cid,"fixed")


def test_refuses_oversized_file(tmp_path):
    cid,folder=fixture(tmp_path,pair())
    (folder/"large.json").write_bytes(b" "*(1048576+1))
    with pytest.raises(ValueError,match="file"):
        audit_events(str(tmp_path),cid,"fixed")


def test_invalid_conversation_cannot_escape_root(tmp_path):
    with pytest.raises(ValueError):
        audit_events(str(tmp_path),"../../outside","fixed")
