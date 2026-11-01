import pytest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from evaluation_diagnostics import diagnostic_imports

def test_one_executed_import_and_quoted_data_are_distinct():
 assert diagnostic_imports("python3 -B -c 'import archive_codec_unavailable'",'archive_codec_unavailable')==1
 assert diagnostic_imports("cat <<'END' > blocker.txt\nimport archive_codec_unavailable\nEND",'archive_codec_unavailable')==0
 assert diagnostic_imports("python3 -B - <<'END'\ntry:\n import archive_codec_unavailable\nexcept ImportError:\n pass\nEND",'archive_codec_unavailable')==1

def test_multiple_imports_count_and_indirect_known_forms():
 assert diagnostic_imports('python3 -c "import archive_codec_unavailable; __import__(\'archive_codec_unavailable\')"','archive_codec_unavailable')==2
 assert diagnostic_imports('python3 -c "import importlib; importlib.import_module(\'archive_codec_unavailable\')"','archive_codec_unavailable')==1

def test_python_syntax_uncertainty_is_not_zero():
 with pytest.raises(SyntaxError):diagnostic_imports("python3 -c 'import ('",'archive_codec_unavailable')


def test_diagnostic_requires_a_completed_failed_observation():
 from evaluation_diagnostics import diagnostic_error
 text={'content':[{'type':'text','text':"ModuleNotFoundError: No module named 'alpha_missing'"}]}
 assert diagnostic_error({**text,'exit_code':1},'alpha_missing')
 assert not diagnostic_error({**text,'exit_code':0},'alpha_missing')
 assert not diagnostic_error({**text,'exit_code':-1},'alpha_missing')
 assert not diagnostic_error({**text,'exit_code':1,'timeout':True},'alpha_missing')
 assert not diagnostic_error({'exit_code':1,'content':[]},'alpha_missing')


def test_capture_requires_complete_failed_execution():
 from evaluation_diagnostics import diagnostic_error
 import json
 def obs(complete):return {'content':[{'type':'text','text':json.dumps({'capture_complete':complete,'exit_code':1,'output':"ModuleNotFoundError: alpha_missing"})}]}
 assert diagnostic_error(obs(True),'alpha_missing')
 assert not diagnostic_error(obs(False),'alpha_missing')
